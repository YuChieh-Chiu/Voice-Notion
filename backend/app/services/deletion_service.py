"""明確清單、限時確認與可恢復垃圾桶操作；不提供永久刪除。"""

import hashlib
import json
import re
import secrets
import time
from contextlib import ExitStack

from app.config import Settings
from app.services.capture_management import CaptureManagement
from app.services.capture_repository import CaptureRepository, utc_timestamp
from app.services.drive_storage import DriveStorage
from app.services.notion_capture_storage import NotionCaptureStorage
from app.services.notion_service import NotionService


class DeletionConflict(ValueError):
    pass


class DeletionService:
    def __init__(
        self, repository: CaptureRepository, settings: Settings, *, drive_factory=DriveStorage
    ):
        self.repository, self.settings, self.drive_factory = repository, settings, drive_factory
        self._storage = None
        with repository.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS deletion_previews (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, expires REAL NOT NULL, data TEXT NOT NULL, status TEXT NOT NULL, result TEXT)"
            )

    @property
    def storage(self):
        if self._storage is None:
            self._storage = self.drive_factory(self.repository, self.settings)
        return self._storage

    def review(self, capture_id, owner_id="owner"):
        record = CaptureManagement(self.repository).review(capture_id, owner_id)
        with self.repository.capture_lock(capture_id):
            self.refresh_note(capture_id)
        return record

    def _original_eligible(self, record):
        files = {f.role: f for f in self.repository.files(record.id)}
        return (
            record.kind == "audio"
            and record.status == "complete"
            and record.reviewed_at
            and all(
                role in files and files[role].state == "uploaded"
                for role in ("original", "transcript", "note")
            )
        )

    def preview(
        self,
        scope,
        *,
        capture_id=None,
        file_id=None,
        file_ids=None,
        owner_id="owner",
        category=None,
        before=None,
    ):
        repository = self.repository
        records, selected = [], []
        if scope == "reviewed":
            offset = 0
            while True:
                batch = repository.list_captures(owner_id, limit=500, offset=offset)
                records.extend(
                    r
                    for r in batch
                    if self._original_eligible(r)
                    and (not category or r.category == category)
                    and (not before or r.created_at[:10] < before)
                )
                if len(batch) < 500:
                    break
                offset += len(batch)
        elif scope in ("file", "files"):
            selected_ids = set(file_ids or []) if scope == "files" else {file_id}
            if not selected_ids or len(selected_ids) > self.settings.DELETION_MAX_FILES:
                raise ValueError("請提供上限內的明確檔案清單")
            selected_files = [repository.file(selected_id) for selected_id in selected_ids]
            records = [
                repository.get(selected_capture, owner_id)
                for selected_capture in sorted({f.capture_id for f in selected_files})
            ]
        elif scope in ("original", "all"):
            records = [repository.get(capture_id, owner_id)]
        else:
            raise ValueError("請明確選擇 original、file、all 或 reviewed")
        snapshots, guards = {}, []
        for record in records:
            if record.destination != "drive" or record.status in ("delete_pending", "deleted"):
                raise ValueError("此操作只提供尚未整筆移除的 Drive 收件")
            if scope in ("original", "reviewed") and not self._original_eligible(record):
                raise ValueError("只清原檔需語音完整原文與摘要已校對；其他內容請明確指定檔案或整筆")
            files = repository.files(record.id)
            if scope in ("original", "reviewed"):
                for retained in files:
                    if retained.role in ("transcript", "note"):
                        remote = self._remote_snapshot(retained)
                        if not remote or remote.get("missing") or remote.get("trashed"):
                            raise DeletionConflict("保留原文或摘要已不可用，不能清理原檔")
                        if (
                            hashlib.sha256(self.storage.read_cloud_file(retained)).hexdigest()
                            != retained.sha256
                        ):
                            raise DeletionConflict("已校對內容在外部變更，請人工確認")
                        guards.append(
                            {
                                "id": retained.id,
                                "capture_id": record.id,
                                "revision": retained.revision,
                                "remote": remote,
                            }
                        )
            snapshots[record.id] = {"revision": record.revision, "files": [f.id for f in files]}
            for file in files:
                if (scope in ("file", "files") and file.id not in selected_ids) or (
                    scope in ("original", "reviewed") and file.role != "original"
                ):
                    continue
                if file.state in ("trashed", "missing"):
                    continue
                if file.state == "trash_pending":
                    raise DeletionConflict("檔案已有待完成清理")
                remote = self._remote_snapshot(file)
                approved_md5 = None
                local_path = repository.directory(record.id) / file.name
                if local_path.exists():
                    content = repository.read_file(file)
                    if hashlib.sha256(content).hexdigest() != file.sha256:
                        raise DeletionConflict("本機檔案已變更")
                    approved_md5 = hashlib.md5(content, usedforsecurity=False).hexdigest()
                selected.append(
                    {
                        "id": file.id,
                        "capture_id": file.capture_id,
                        "role": file.role,
                        "name": file.name,
                        "bytes": file.size_bytes,
                        "revision": file.revision,
                        "cloud_id": file.cloud_id,
                        "remote": remote,
                        "approved_md5": approved_md5,
                        "state": file.state,
                    }
                )
        if not selected:
            raise ValueError("沒有符合條件的檔案")
        if len(selected) > self.settings.DELETION_MAX_FILES:
            raise ValueError("清單超過上限，請用主題或日期縮小範圍")
        token = secrets.token_urlsafe(24)
        expires = time.time() + self.settings.DELETION_PREVIEW_SECONDS
        data = {"scope": scope, "files": selected, "captures": snapshots, "guards": guards}
        with repository.connection() as conn:
            conn.execute(
                "INSERT INTO deletion_previews(id,owner_id,expires,data,status) VALUES(?,?,?,?,'pending')",
                (self._token_hash(token), owner_id, expires, json.dumps(data)),
            )
        return {
            "token": token,
            "expires_at": expires,
            "files": selected,
            "count": len(selected),
            "total_bytes": sum(f["bytes"] for f in selected),
            "warning": "僅移至垃圾桶，仍占容量。指定檔案／整筆可能移除唯一內容，原檔移除後不能重轉錄。",
        }

    def _remote_snapshot(self, file):
        if not file.cloud_id:
            return None
        metadata = self.storage.metadata(file.cloud_id)
        if metadata is None:
            return {"missing": True}
        self.storage.verify_file(file, metadata)
        if not metadata.get("trashed"):
            self.storage.assert_private(metadata)
        return {
            key: metadata.get(key)
            for key in ("id", "version", "size", "md5Checksum", "trashed", "parents")
        }

    @staticmethod
    def _token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def _load(self, token, owner_id):
        with self.repository.connection() as conn:
            row = conn.execute(
                "SELECT * FROM deletion_previews WHERE id=? AND owner_id=?",
                (self._token_hash(token), owner_id),
            ).fetchone()
        if not row:
            raise KeyError("Confirmation not found")
        return dict(row)

    def cancel(self, token, owner_id="owner"):
        row = self._load(token, owner_id)
        with self.repository.connection() as conn:
            conn.execute(
                "UPDATE deletion_previews SET status='cancelled' WHERE id=? AND status='pending'",
                (row["id"],),
            )
        return {"status": self._load(token, owner_id)["status"]}

    def confirm(self, token, owner_id="owner", *, max_result_files=None):
        row = self._load(token, owner_id)
        data = json.loads(row["data"])
        if max_result_files is not None and len(data["files"]) > max_result_files:
            raise ValueError(
                "這批明細太長，LINE未套用本次確認；請透過管理 API 完成操作或查看完整結果"
            )
        if row["status"] == "done":
            return json.loads(row["result"])
        if row["status"] == "cancelled":
            raise DeletionConflict("清單已取消")
        if row["status"] == "pending":
            if time.time() > row["expires"]:
                raise DeletionConflict("確認已過期，請重新預覽")
            self.validate_retained(data)
            for item in data["files"]:
                file = self.repository.file(item["id"])
                if self._remote_snapshot(file) != item["remote"]:
                    raise DeletionConflict("Drive 檔案已變更，請重新預覽")
            # 意圖在取得 worker 鎖前寫入；在途任務即使尚未停止也不能恢復已移除狀態。
            with self.repository.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current = conn.execute(
                    "SELECT status FROM deletion_previews WHERE id=?", (row["id"],)
                ).fetchone()
                if current["status"] != "pending":
                    raise DeletionConflict("確認狀態已變更，請重試查詢")
                for capture_id, snapshot in data["captures"].items():
                    capture = conn.execute(
                        "SELECT revision,status,owner_id FROM captures WHERE id=?", (capture_id,)
                    ).fetchone()
                    files = conn.execute(
                        "SELECT id FROM capture_files WHERE capture_id=? ORDER BY role",
                        (capture_id,),
                    ).fetchall()
                    if (
                        capture["owner_id"] != owner_id
                        or capture["revision"] != snapshot["revision"]
                        or [f["id"] for f in files] != snapshot["files"]
                    ):
                        raise DeletionConflict("收件狀態已變更，請重新預覽")
                for item in data["files"]:
                    changed = conn.execute(
                        "UPDATE capture_files SET state='trash_pending',revision=revision+1 WHERE id=? AND revision=?",
                        (item["id"], item["revision"]),
                    ).rowcount
                    if changed != 1:
                        raise DeletionConflict("檔案狀態已變更，請重新預覽")
                if data["scope"] == "all":
                    for capture_id in data["captures"]:
                        conn.execute(
                            "UPDATE captures SET status='delete_pending',revision=revision+1 WHERE id=?",
                            (capture_id,),
                        )
                conn.execute(
                    "UPDATE deletion_previews SET status='running' WHERE id=?", (row["id"],)
                )
        try:
            result = self._execute(data)
        except DeletionConflict:
            # 保留內容失效時取消尚未執行的項目；已進垃圾桶的項目不復原、不再執行。
            with self.repository.connection() as conn:
                for item in data["files"]:
                    conn.execute(
                        "UPDATE capture_files SET state=?,revision=revision+1 WHERE id=? AND state='trash_pending'",
                        (item["state"], item["id"]),
                    )
                conn.execute(
                    "UPDATE deletion_previews SET status='cancelled' WHERE id=?", (row["id"],)
                )
            raise
        with self.repository.connection() as conn:
            conn.execute(
                "UPDATE deletion_previews SET status=?,result=? WHERE id=?",
                ("done" if result["complete"] else "running", json.dumps(result), row["id"]),
            )
        return result

    def validate_retained(self, data):
        for guard in data.get("guards", []):
            original = next(
                f for f in self.repository.files(guard["capture_id"]) if f.role == "original"
            )
            if original.state in ("trashed", "missing"):
                continue
            retained = self.repository.file(guard["id"])
            if retained.state != "uploaded" or retained.revision != guard["revision"]:
                raise DeletionConflict("保留原文或摘要已變更，請重新確認後預覽")
            remote = self._remote_snapshot(retained)
            # Drive version 也包含不可見的伺服器 metadata 變動；實際內容另以 SHA256 核對。
            if (
                not isinstance(remote, dict)
                or {key: value for key, value in remote.items() if key != "version"}
                != {key: value for key, value in guard["remote"].items() if key != "version"}
                or hashlib.sha256(self.storage.read_cloud_file(retained)).hexdigest()
                != retained.sha256
            ):
                raise DeletionConflict("保留原文或摘要已變更，請重新確認後預覽")

    def _execute(self, data):
        outcomes, metadata_errors = [], []
        with ExitStack() as stack:
            for capture_id in sorted(data["captures"]):
                stack.enter_context(self.repository.capture_lock(capture_id))
            self.validate_retained(data)
            for item in data["files"]:
                file = self.repository.file(item["id"])
                try:
                    state = self.trash_file(
                        file, expected=item["remote"], approved_md5=item.get("approved_md5")
                    )
                    outcomes.append({"file_id": file.id, "status": state})
                except Exception as exc:
                    outcomes.append(
                        {"file_id": file.id, "status": "failed", "error_code": type(exc).__name__}
                    )
            for capture_id in data["captures"]:
                if data["scope"] == "all":
                    if all(
                        f.state in ("trashed", "missing") for f in self.repository.files(capture_id)
                    ):
                        self.finish_retraction(capture_id)
                else:
                    try:
                        self.refresh_note(capture_id)
                    except Exception as exc:
                        metadata_errors.append(
                            {"capture_id": capture_id, "error_code": type(exc).__name__}
                        )
        return {
            "complete": not metadata_errors and all(o["status"] != "failed" for o in outcomes),
            "files": outcomes,
            "metadata_errors": metadata_errors,
            "message": "垃圾桶仍占容量；永久刪除請至 Drive 操作。",
        }

    def trash_file(self, file, *, expected=None, approved_md5=None, reason="manual_clear"):
        if file.state in ("trashed", "missing"):
            self.remember_removal(file, reason)
            return file.state
        state = "trashed"
        if file.cloud_id:
            metadata = self.storage.metadata(file.cloud_id)
            if metadata is None:
                state = "missing"
            else:
                self.storage.verify_file(file, metadata)
                if not metadata.get("trashed"):
                    if expected is not None and self._remote_snapshot(file) != expected:
                        # 預覽時尚未落地的固定 ID，可在確認後才收到在途上傳的同一份位元組。
                        same_inflight = (
                            expected.get("missing")
                            and approved_md5
                            and (
                                metadata.get("md5Checksum") == approved_md5
                                and int(metadata.get("size", -1)) == file.size_bytes
                            )
                        )
                        if not same_inflight:
                            raise DeletionConflict("Drive 檔案在確認後變更，已停止此檔清理")
                    self.storage.assert_private(metadata)
                    self.storage.service.files().update(
                        fileId=file.cloud_id, body={"trashed": True}, fields="id,trashed"
                    ).execute(num_retries=2)
        (self.repository.directory(file.capture_id) / file.name).unlink(missing_ok=True)
        self.repository.update_file(file.id, state=state, cloud_url=None)
        self.remember_removal(file, reason)
        self.repository.save_checkpoint(file.capture_id, "drive_upload:" + file.id, None)
        return state

    def remember_removal(self, file, reason):
        key = "removed:" + file.id
        if not self.repository.checkpoint(file.capture_id, key):
            self.repository.save_checkpoint(
                file.capture_id, key, {"recorded_at": utc_timestamp(), "reason": reason}
            )

    def refresh_note(self, capture_id):
        record = self.repository.get(capture_id)
        if record.destination != "drive":
            return
        files = {f.role: f for f in self.repository.files(capture_id)}
        note = files.get("note")
        if not note or note.state != "uploaded":
            return
        pending = self.repository.checkpoint(capture_id, "note_metadata_pending")
        local_path = self.repository.directory(capture_id) / note.name
        if pending and local_path.exists():
            remote_content = self.storage.read_cloud_file(note)
            if not isinstance(pending, dict) or hashlib.sha256(remote_content).hexdigest() not in (
                pending["base_sha256"],
                note.sha256,
            ):
                raise DeletionConflict("待更新期間筆記在外部變更，已保留待處理稿並停止覆寫")
            note = self.storage.upload(note, replace=True)
            self.repository.save_checkpoint(capture_id, "note_metadata_pending", None)
            local_path.unlink(missing_ok=True)
        content = self.storage.read_cloud_file(note)
        if hashlib.sha256(content).hexdigest() != note.sha256:
            raise DeletionConflict("筆記已在外部編輯，請人工更新來源狀態")
        text = content.decode("utf-8")
        original_removed = files["original"].state in ("trashed", "missing")
        source_removed = files["original" if record.kind == "text" else "transcript"].state in (
            "trashed",
            "missing",
        )
        updates = {
            "review_status": "confirmed" if record.reviewed_at else "pending",
            "original_status": files["original"].state if original_removed else "present",
            "source_status": "removed" if source_removed else "present",
        }
        if original_removed:
            removal = (
                self.repository.checkpoint(capture_id, "removed:" + files["original"].id) or {}
            )
            updates["original_removal_recorded_at"] = removal.get("recorded_at")
            updates["original_removal_reason"] = removal.get("reason")
        for key, value in updates.items():
            pattern = r"^" + key + r":[^\n]*$"
            line = key + ": " + json.dumps(value)
            text = (
                re.sub(pattern, line, text, count=1, flags=re.M)
                if re.search(pattern, text, re.M)
                else text.replace("\n---\n", "\n" + line + "\n---\n", 1)
            )
        for heading, removed in [("原始來源", original_removed), ("完整原文", source_removed)]:
            if removed:
                text = re.sub(
                    r"\n## " + heading + r"\n.*?(?=\n## |\Z)",
                    "\n## " + heading + "\n已由使用者移除；連結已撤下。\n",
                    text,
                    flags=re.S,
                )
        updated = text.encode()
        if updated != content:
            self.repository.save_checkpoint(
                capture_id,
                "note_metadata_pending",
                {
                    "base_sha256": hashlib.sha256(content).hexdigest(),
                },
            )
            note = self.repository.save_artifact(capture_id, "note", updated)
            self.storage.upload(note, replace=True)
            self.repository.save_checkpoint(capture_id, "note_metadata_pending", None)
            local_path.unlink(missing_ok=True)

    def finish_retraction(self, capture_id):
        self.repository.remove_local_files(capture_id)
        with self.repository.connection() as conn:
            conn.execute("DELETE FROM capture_checkpoints WHERE capture_id=?", (capture_id,))
            conn.execute(
                "UPDATE captures SET status='deleted',original_name='',source_url=NULL,category=NULL,reviewed_at=NULL,notion_url=NULL,encrypted_context=NULL,error_code=NULL,notified=1,revision=revision+1 WHERE id=?",
                (capture_id,),
            )
            conn.execute(
                "UPDATE capture_files SET cloud_url=NULL,sha256='',size_bytes=0 WHERE capture_id=?",
                (capture_id,),
            )

    def retract(self, capture_id):
        if self.repository.get(capture_id, "owner").status == "deleted":
            return
        self.repository.update_capture(capture_id, status="delete_pending")
        with self.repository.capture_lock(capture_id):
            record = self.repository.get(capture_id, "owner")
            if record.destination == "notion":
                NotionCaptureStorage(self.repository, NotionService()).retract(capture_id)
                for file in self.repository.files(capture_id):
                    self.repository.update_file(file.id, state="trashed", cloud_url=None)
            else:
                for file in self.repository.files(capture_id):
                    self.repository.update_file(file.id, state="trash_pending")
                    self.trash_file(self.repository.file(file.id), reason="line_unsend")
            self.finish_retraction(capture_id)
