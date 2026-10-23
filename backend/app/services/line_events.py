"""加密且持久化的 LINE 事件收件匣；佇列只攜帶事件 ID。"""

import fcntl
import hashlib
import os
import re
from contextlib import contextmanager

from app.config import Settings
from app.core.security import TaskSecurity
from app.services.audio_validator import validate_audio_format
from app.services.capture_management import CaptureManagement
from app.services.capture_repository import CaptureRepository
from app.services.deletion_service import DeletionService
from app.services.line_client import LineClient

EVENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
COMMAND_HELP = """Note-Goblin 指令說明
/help 或 /：查看這份說明。
/files C代號（或 /file C代號）：查看一筆收件的檔案、狀態與連結。
/review C代號：標記已人工校對完整原文與摘要，不會刪檔。
/clear C代號 original：預覽清理已校對語音的原始音檔，保留逐字稿與摘要。
/clear C代號 all：預覽清理整筆受管檔案，可能移除唯一內容。
/clear file F代號 [F代號...]：預覽清理明確指定的一個或多個檔案。
/clear reviewed [topic=主題] [before=YYYY-MM-DD]：批次預覽已校對語音的原始音檔；可縮小主題及日期範圍。
before 採 UTC 收件日期，只選指定日期之前的收件。
/confirm token：確認執行該份限時預覽；請使用預覽提供的實際 token。
/cancel token：取消該份尚未執行的預覽。

語音原檔捷徑需先人工校對；未校對或只有原檔的收件，請明確指定檔案或整筆。
Drive 清理只移到垃圾桶，仍占儲存空間；不會永久刪除或清空垃圾桶。"""


class LineEventStore:
    def __init__(self, repository: CaptureRepository):
        self.repository = repository
        with repository.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS line_events (id TEXT PRIMARY KEY, "
                "payload TEXT, status TEXT NOT NULL DEFAULT 'pending', error_code TEXT)"
            )
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(line_events)")}
            if "message_id" not in columns:
                conn.execute("ALTER TABLE line_events ADD COLUMN message_id TEXT")

    def revoked(self, message_id):
        return bool(self.repository.system_value("line_revoked:" + message_id))

    def revoke(self, message_id):
        if not isinstance(message_id, str) or not EVENT_ID.fullmatch(message_id):
            raise ValueError("Invalid LINE unsend message ID")
        self.repository.save_system_value("line_revoked:" + message_id, True)
        with self.repository.connection() as conn:
            conn.execute(
                "UPDATE captures SET status='delete_pending',revision=revision+1 WHERE owner_id='owner' AND source_key=? AND status!='deleted'",
                ("line:" + message_id,),
            )
            conn.execute(
                "UPDATE line_events SET status='complete',payload=NULL WHERE message_id=?",
                (message_id,),
            )

    @contextmanager
    def lock(self, event_id):
        name = "line-" + hashlib.sha256(event_id.encode()).hexdigest() + ".lock"
        fd = os.open(self.repository.locks / name, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def accept(self, event: dict) -> str | None:
        event_id = event.get("webhookEventId", "")
        if not isinstance(event_id, str) or not EVENT_ID.fullmatch(event_id):
            raise ValueError("Invalid webhook event ID")
        message_id = (
            event.get("message", {}).get("id", "") if event.get("type") == "message" else ""
        )
        if message_id and self.revoked(message_id):
            return None
        encrypted = TaskSecurity.encrypt_payload(event)
        with self.repository.connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO line_events(id,payload,message_id) VALUES(?,?,?)",
                (event_id, encrypted, message_id),
            )
            status = conn.execute(
                "SELECT status FROM line_events WHERE id=?", (event_id,)
            ).fetchone()["status"]
        return event_id if status != "complete" else None

    def load(self, event_id):
        with self.repository.connection() as conn:
            row = conn.execute("SELECT * FROM line_events WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise KeyError("Event not found")
        return dict(row)

    def complete(self, event_id):
        with self.repository.connection() as conn:
            conn.execute(
                "UPDATE line_events SET status='complete',payload=NULL,error_code=NULL WHERE id=?",
                (event_id,),
            )


class LineEventProcessor:
    def __init__(self, repository: CaptureRepository, settings: Settings, enqueue, *, client=None):
        self.repository, self.settings, self.enqueue = repository, settings, enqueue
        self.store, self.client = LineEventStore(repository), client or LineClient(settings)

    def process(self, event_id: str):
        with self.store.lock(event_id):
            stored = self.store.load(event_id)
            if stored["status"] == "complete":
                return
            event = TaskSecurity.decrypt_payload(stored["payload"])
            source = event.get("source", {})
            if source.get("type") != "user" or source.get("userId") != self.settings.LINE_USER_ID:
                raise ValueError("Unauthorized LINE event")
            if event["type"] == "unsend":
                message_id = event.get("unsend", {}).get("messageId", "")
                self.store.revoke(message_id)
                record = self.repository.find_source("owner", "line:" + message_id)
                if record:
                    DeletionService(self.repository, self.settings).retract(record.id)
                self.client.push(
                    "收回已處理：服務管理內容已撤下。另存副本、Atlas 衍生內容與供應商備份不在此範圍。",
                    key="event:" + event_id,
                )
            elif event["type"] == "postback":
                data = event.get("postback", {}).get("data", "")
                command = "/help"
                for prefix, verb in [
                    ("review:", "/review "),
                    ("confirm:", "/confirm "),
                    ("cancel:", "/cancel "),
                    ("clear-original:", "/clear "),
                ]:
                    if data.startswith(prefix):
                        command = (
                            verb
                            + data[len(prefix) :]
                            + (" original" if prefix == "clear-original:" else "")
                        )
                        break
                self.command(command, event_id)
            elif event["type"] == "message":
                message = event.get("message", {})
                if message.get("type") == "text" and message.get("text", "").startswith("/"):
                    self.command(message["text"], event_id)
                else:
                    self.capture(message, event_id)
            self.store.complete(event_id)

    def command(self, command: str, event_id: str):
        parts = command.split()
        management = CaptureManagement(self.repository)
        deletion = DeletionService(self.repository, self.settings)
        actions = []
        result = self.repository.system_value("line_command:" + event_id)
        if result:
            self.client.push(result["text"], key="event:" + event_id, actions=result["actions"])
            return
        try:
            if len(parts) == 2 and parts[0] in ("/files", "/file"):
                text = management.describe(parts[1])
                record = self.repository.get(parts[1], owner_id="owner")
                files = {file.role: file for file in self.repository.files(record.id)}
                required_roles = {
                    "text": {"original", "note"},
                    "audio": {"original", "transcript", "note"},
                }.get(record.kind)
                if (
                    record.status == "complete"
                    and required_roles
                    and required_roles <= files.keys()
                    and all(file.state == "uploaded" for file in files.values())
                ):
                    clear_original = record.kind == "audio" and bool(record.reviewed_at)
                    actions = [
                        {
                            "type": "postback",
                            "label": "預覽清理原檔" if clear_original else "確認全文與摘要正確",
                            "data": ("clear-original:" if clear_original else "review:")
                            + record.id,
                        }
                    ]
            elif len(parts) == 2 and parts[0] == "/review":
                record = deletion.review(parts[1])
                text = f"{record.id} 已確認完整原文與摘要；尚未清理任何檔案。"
                if record.destination == "drive" and record.kind == "audio":
                    actions = [
                        {
                            "type": "postback",
                            "label": "預覽清理原檔",
                            "data": "clear-original:" + record.id,
                        }
                    ]
            elif parts and parts[0] == "/clear":
                if len(parts) >= 2 and parts[1] == "reviewed":
                    options = dict(p.split("=", 1) for p in parts[2:])
                    if options.keys() - {"topic", "before"}:
                        raise ValueError("未知篩選條件")
                    preview = deletion.preview(
                        "reviewed", category=options.get("topic"), before=options.get("before")
                    )
                elif len(parts) >= 3 and parts[1] == "file":
                    preview = deletion.preview("files", file_ids=parts[2:])
                elif len(parts) == 3:
                    preview = deletion.preview(parts[2], capture_id=parts[1])
                else:
                    raise ValueError(
                        "請指定 /clear C代號 original 或 all，或 /clear file F代號、/clear reviewed"
                    )
                text = "\n".join(
                    [
                        f"清理預覽：{preview['count']} 檔／{preview['total_bytes']} bytes",
                        *[
                            f"{f['capture_id']} {f['id']} {f['role']} {f['bytes']} bytes"
                            for f in preview["files"]
                        ],
                        preview["warning"],
                        "確認限時有效；確認只處理這份清單。",
                        "看不到按鈕時，可複製並送出以下指令：",
                        "/confirm " + preview["token"],
                        "/cancel " + preview["token"],
                    ]
                )
                if len(text) > 4400:
                    deletion.cancel(preview["token"])
                    raise ValueError("清單太長，請用 topic=主題 或 before=YYYY-MM-DD 縮小範圍")
                actions = [
                    {
                        "type": "postback",
                        "label": "確認移到垃圾桶",
                        "data": "confirm:" + preview["token"],
                    },
                    {"type": "postback", "label": "取消", "data": "cancel:" + preview["token"]},
                ]
            elif len(parts) == 2 and parts[0] == "/confirm":
                outcome = deletion.confirm(parts[1])
                text = (
                    "清理完成"
                    if outcome["complete"]
                    else "部分清理失敗；可重送本次確認以重試失敗項目"
                ) + "\n"
                text += (
                    "\n".join(f"{f['file_id']}: {f['status']}" for f in outcome["files"])
                    + "\n"
                    + outcome["message"]
                )
                if outcome["metadata_errors"]:
                    text += (
                        "\n筆記來源狀態尚未更新："
                        + ", ".join(item["capture_id"] for item in outcome["metadata_errors"])
                        + "。網路故障可重試；若外部編輯衝突，需依部署指南人工調和，請勿持續重點確認。"
                    )
            elif len(parts) == 2 and parts[0] == "/cancel":
                text = "清理確認狀態：" + deletion.cancel(parts[1])["status"]
            elif parts and parts[0] in ("/files", "/file"):
                text = "用法：/files C代號（或 /file C代號），查看指定收件的檔案與狀態。"
            elif len(parts) == 1 and parts[0] in ("/help", "/"):
                text = COMMAND_HELP
            else:
                text = "無法辨識指令；輸入 /help 查看可用指令。"
        except (ValueError, KeyError) as exc:
            text = str(exc) if isinstance(exc, ValueError) else "找不到可操作的收件或確認清單。"
        self.repository.save_system_value(
            "line_command:" + event_id, {"text": text, "actions": actions}
        )
        self.client.push(text, key="event:" + event_id, actions=actions)

    def capture(self, message: dict, event_id: str):
        kind, message_id = message.get("type"), message.get("id", "")
        if not isinstance(message_id, str) or not EVENT_ID.fullmatch(message_id):
            raise ValueError("Invalid LINE message ID")
        if self.store.revoked(message_id):
            return
        if kind not in ("text", "audio", "image", "file"):
            self.client.push("目前支援文字、語音、圖片與檔案。", key="event:" + event_id)
            return
        if self.settings.CAPTURE_DESTINATION == "notion" and kind in ("image", "file"):
            self.client.push(
                "Notion 模式只保存文字與語音轉錄，不收附件；未改存其他目的地。",
                key="event:" + event_id,
            )
            return
        key = "line:" + message_id
        existing = self.repository.find_source("owner", key)
        if existing:
            if existing.status not in ("complete", "delete_pending", "deleted"):
                self.enqueue(existing.id)
            return
        if kind == "text":
            text = message.get("text", "")
            if (
                not isinstance(text, str)
                or not text.strip()
                or len(text) > self.settings.CAPTURE_MAX_TEXT_CHARS
            ):
                raise ValueError("LINE text exceeds limit or is empty")
            content, name, mime, extension = text.encode(), "original.txt", "text/plain", ".txt"
        else:
            if message.get("contentProvider", {}).get("type", "line") != "line":
                raise ValueError("External content providers are not supported")
            if int(message.get("fileSize", 0)) > self.settings.CAPTURE_MAX_BYTES:
                raise ValueError("LINE file exceeds limit")
            content = self.client.download(message_id)
            name = message.get("fileName", "original")
            extension, mime = ".bin", "application/octet-stream"
            if kind == "audio":
                extension = validate_audio_format(content)
                mime = "audio/" + extension[1:]
            elif content.startswith(b"%PDF-"):
                extension, mime = ".pdf", "application/pdf"
            elif content.startswith(b"\x89PNG\r\n\x1a\n"):
                extension, mime = ".png", "image/png"
            elif content.startswith(b"\xff\xd8\xff"):
                extension, mime = ".jpg", "image/jpeg"
            if name == "original":
                name += extension
        if len(content) > self.settings.CAPTURE_MAX_BYTES:
            raise ValueError("LINE content exceeds limit")
        record, _ = self.repository.create(
            owner_id="owner",
            channel="line",
            kind=kind,
            destination=self.settings.CAPTURE_DESTINATION,
            content=content,
            original_name=name,
            mime_type=mime,
            stored_extension=extension,
            source_key=key,
        )
        if self.store.revoked(message_id):
            DeletionService(self.repository, self.settings).retract(record.id)
            return
        self.enqueue(record.id)
