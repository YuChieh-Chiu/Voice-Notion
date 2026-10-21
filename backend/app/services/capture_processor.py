"""所有入口共用的文字／語音處理，保存模組只處理目的地差異。"""

import json
from functools import lru_cache

from app.config import Settings
from app.core.security import TaskSecurity
from app.schemas.context import UserContext
from app.services.capture_repository import REMOVED_CAPTURE_STATES, CaptureRepository
from app.services.llm_service import LLMService
from app.services.notification_service import NotificationService
from app.services.notion_capture_storage import NotionCaptureStorage
from app.services.notion_service import NotionService
from app.services.stt_service import STTService


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
    ):
        self.repository, self.settings = repository, settings
        self.stt_factory, self.llm_factory = stt_factory, llm_factory
        self.notion_factory, self.notifier_factory = notion_factory, notifier_factory

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
                    raise
            record = repository.get(capture_id)
            result = {
                "status": "success",
                "capture_id": capture_id,
                "notion_url": record.notion_url,
                "title": repository.checkpoint(capture_id, "title") or capture_id,
            }
            # 目的地已可靠保存，通知重試不再需要本機音訊／逐字稿。
            repository.remove_local_files(capture_id)
            if not record.notified:
                notifier = self.notifier_factory(context=context)
                notifier.push_message(result["title"], record.notion_url or "")
                repository.update_capture(capture_id, notified=True, encrypted_context=None)
            return result

    def _process_content(self, capture_id: str, context: UserContext | None) -> None:
        repository = self.repository
        record = repository.get(capture_id)
        if record.destination == "drive":
            raise ValueError("Drive storage is not configured in this stage")
        if record.kind not in ("text", "audio"):
            raise ValueError("Notion attachments are not supported")
        artifacts = {file.role: file for file in repository.files(capture_id)}
        if record.kind == "text":
            source_text = repository.read_file(artifacts["original"]).decode("utf-8")
        elif "transcript" in artifacts:
            source_text = repository.read_file(artifacts["transcript"]).decode("utf-8")
        else:
            source_text = self.stt_factory().transcribe(
                str(repository.directory(capture_id) / artifacts["original"].name)
            )
            if not source_text.strip() or len(source_text) > self.settings.CAPTURE_MAX_TEXT_CHARS:
                raise ValueError("Transcript is empty or exceeds the configured text limit")
            repository.save_artifact(capture_id, "transcript", source_text.encode())
        notion = self.notion_factory(context=context)
        llm = self.llm_factory(context=context)
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
            if not summary.strip() or len(summary) > self.settings.CAPTURE_MAX_TEXT_CHARS:
                raise ValueError("Summary is empty or exceeds the configured text limit")
            note = "---\ncapture_id: " + capture_id + "\ncreated_at: " + record.created_at
            note += "\nauthorship: not_confirmed\noriginal_name: " + json.dumps(
                record.original_name, ensure_ascii=False
            )
            note += "\n---\n\n## 摘要\n" + summary
            repository.save_artifact(capture_id, "note", note.encode())
        url = NotionCaptureStorage(repository, notion).save(
            capture_id, routing, source_text, summary
        )
        repository.update_capture(capture_id, notion_url=url, status="complete")
