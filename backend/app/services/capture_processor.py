"""所有入口共用的文字／語音處理，保存模組只處理目的地差異。"""

import json
from functools import lru_cache

from app.config import Settings
from app.core.security import TaskSecurity
from app.schemas.context import UserContext
from app.services.capture_repository import REMOVED_CAPTURE_STATES, CaptureRepository
from app.services.drive_storage import DriveStorage
from app.services.llm_service import LLMService
from app.services.notification_service import NotificationService
from app.services.notion_capture_storage import NotionCaptureStorage
from app.services.notion_service import NotionService
from app.services.stt_service import STTService
from app.services.topic_router import TopicRouter


@lru_cache(maxsize=1)
def worker_stt_service() -> STTService:
    return STTService()


class CaptureProcessor:
    def __init__(
        self,
        repository: CaptureRepository,
        settings: Settings,
        *,
        stt_factory=worker_stt_service,
        llm_factory=LLMService,
        notion_factory=NotionService,
        notifier_factory=NotificationService,
        drive_factory=DriveStorage,
        router_factory=TopicRouter,
    ):
        self.repository, self.settings = repository, settings
        self.stt_factory, self.llm_factory = stt_factory, llm_factory
        self.notion_factory, self.notifier_factory = notion_factory, notifier_factory
        self.drive_factory, self.router_factory = drive_factory, router_factory

    def process(self, capture_id: str) -> dict:
        repository = self.repository
        with repository.capture_lock(capture_id):
            record = repository.get(capture_id)
            if record.status in REMOVED_CAPTURE_STATES:
                return {"status": "removed", "capture_id": capture_id}
            context = (
                UserContext(**TaskSecurity.decrypt_payload(record.encrypted_context))
                if record.encrypted_context
                else None
            )
            if record.status != "complete":
                repository.update_capture(capture_id, status="processing", error_code=None)
                try:
                    self._process_content(capture_id, context)
                except Exception as exc:
                    if repository.get(capture_id).status not in REMOVED_CAPTURE_STATES:
                        repository.update_capture(
                            capture_id, status="failed", error_code=type(exc).__name__
                        )
                        if record.destination == "drive" and any(
                            f.role == "original" and f.state == "uploaded"
                            for f in repository.files(capture_id)
                        ):
                            try:
                                self.notifier_factory(context=context).push_partial(capture_id)
                            except Exception:
                                # 保留原始處理失敗供重試；通知本身使用獨立、穩定 retry key。
                                pass
                    raise
            record = repository.get(capture_id)
            if record.status in REMOVED_CAPTURE_STATES:
                return {"status": "removed", "capture_id": capture_id}
            url = record.notion_url or (
                f"https://drive.google.com/drive/folders/{record.folder_id}"
                if record.folder_id
                else ""
            )
            result = {
                "status": "success",
                "capture_id": capture_id,
                "notion_url": record.notion_url,
                "url": url,
                "title": repository.checkpoint(capture_id, "title") or capture_id,
            }
            # 目的地已可靠保存，通知重試不再需要本機音訊／逐字稿。
            if not repository.checkpoint(capture_id, "note_metadata_pending"):
                repository.remove_local_files(capture_id)
            if not record.notified:
                self.notifier_factory(context=context).push_message(
                    result["title"],
                    url,
                    capture_id=capture_id,
                    original_only=record.kind in ("image", "file"),
                )
                repository.update_capture(capture_id, notified=True, encrypted_context=None)
            return result

    def _process_content(self, capture_id: str, context: UserContext | None) -> None:
        record = self.repository.get(capture_id)
        if record.destination == "drive":
            self._process_drive(capture_id, context)
        else:
            self._process_notion(capture_id, context)

    def _source_text(self, capture_id: str) -> str:
        repository = self.repository
        record = repository.get(capture_id)
        artifacts = {file.role: file for file in repository.files(capture_id)}
        if record.kind == "text":
            return repository.read_file(artifacts["original"]).decode("utf-8")
        if "transcript" in artifacts:
            return repository.read_file(artifacts["transcript"]).decode("utf-8")
        source_text = self.stt_factory().transcribe(
            str(repository.directory(capture_id) / artifacts["original"].name)
        )
        if not source_text.strip() or len(source_text) > self.settings.CAPTURE_MAX_TEXT_CHARS:
            raise ValueError("Transcript is empty or exceeds the configured text limit")
        repository.save_artifact(capture_id, "transcript", source_text.encode())
        return source_text

    def _validate_summary(self, summary: str) -> None:
        if not summary.strip() or len(summary) > self.settings.CAPTURE_MAX_TEXT_CHARS:
            raise ValueError("Summary is empty or exceeds the configured text limit")

    def _process_notion(self, capture_id: str, context: UserContext | None) -> None:
        repository = self.repository
        record = repository.get(capture_id)
        if record.kind not in ("text", "audio"):
            raise ValueError("Notion attachments are not supported")
        source_text = self._source_text(capture_id)
        artifacts = {file.role: file for file in repository.files(capture_id)}
        notion, llm = self.notion_factory(context=context), self.llm_factory(context=context)
        routing = repository.checkpoint(capture_id, "routing")
        if routing is None:
            routing = llm.route(source_text, notion.sync_page_tree())
            repository.save_checkpoint(capture_id, "routing", routing)
            repository.save_checkpoint(capture_id, "title", routing["title"])
        if "note" in artifacts:
            summary = (
                repository.read_file(artifacts["note"]).decode("utf-8").split("\n## 摘要\n", 1)[1]
            )
        else:
            summary = llm.summarize(source_text, routing.get("template_type", "general"))
            self._validate_summary(summary)
            note = "---\ncapture_id: " + capture_id + "\ncreated_at: " + record.created_at
            note += "\nauthorship: not_confirmed\noriginal_name: " + json.dumps(
                record.original_name, ensure_ascii=False
            )
            repository.save_artifact(
                capture_id, "note", (note + "\n---\n\n## 摘要\n" + summary).encode()
            )
        url = NotionCaptureStorage(repository, notion).save(
            capture_id, routing, source_text, summary
        )
        repository.update_capture(capture_id, notion_url=url, status="complete")

    def _process_drive(self, capture_id: str, context: UserContext | None) -> None:
        repository = self.repository
        record = repository.get(capture_id)
        storage = self.drive_factory(repository, self.settings)
        artifacts = {file.role: file for file in repository.files(capture_id)}
        original = storage.upload(artifacts["original"])
        router = self.router_factory(self.settings)
        if record.kind in ("image", "file"):
            repository.update_capture(
                capture_id, category=router.classify(record.original_name, metadata_only=True)
            )
            storage.ensure_capture_folder(capture_id)
            repository.save_checkpoint(
                capture_id, "title", record.original_name + "（原檔已保存，未解析）"
            )
            repository.update_capture(capture_id, status="complete")
            return
        source_text = self._source_text(capture_id)
        if record.category is None:
            repository.update_capture(capture_id, category=router.classify(source_text))
        storage.ensure_capture_folder(capture_id)
        artifacts = {file.role: file for file in repository.files(capture_id)}
        if record.kind == "audio":
            storage.upload(artifacts["transcript"])
        if "note" not in artifacts:
            summary = self.llm_factory(context=context).summarize(source_text, "general")
            self._validate_summary(summary)
            repository.save_checkpoint(
                capture_id, "title", source_text.splitlines()[0][:80] or capture_id
            )
            repository.save_artifact(
                capture_id, "note", self.render_drive_note(capture_id, original, summary).encode()
            )
        storage.upload(next(file for file in repository.files(capture_id) if file.role == "note"))
        repository.update_capture(capture_id, status="complete")

    def render_drive_note(self, capture_id: str, original, summary: str) -> str:
        record = self.repository.get(capture_id)
        source_file = (
            original
            if record.kind == "text"
            else next(
                file for file in self.repository.files(capture_id) if file.role == "transcript"
            )
        )
        metadata = {
            "capture_id": capture_id,
            "received_at": record.created_at,
            "captured_via": record.channel,
            "authorship": "not_confirmed",
            "source_url": None,
            "original_filename": record.original_name,
            "original_status": "present",
            "review_status": "pending",
            "category": record.category,
            "original_file_id": original.id,
            "original_cloud_id": original.cloud_id,
        }
        frontmatter = (
            "---\n"
            + "\n".join(
                key + ": " + json.dumps(value, ensure_ascii=False)
                for key, value in metadata.items()
            )
            + "\n---\n"
        )
        return (
            frontmatter
            + "\n## 完整原文\n[查看完整原文](./"
            + source_file.name
            + ")\n雲端連結："
            + (source_file.cloud_url or "")
            + "\n\n## 原始來源\n[查看收到的原檔](./"
            + original.name
            + ")\n雲端連結："
            + (original.cloud_url or "")
            + "\n\n## 摘要\n"
            + summary
        )
