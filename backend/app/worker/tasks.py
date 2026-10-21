"""可恢復的共用收件任務；broker 只保存來源 ID。"""

import hashlib
from pathlib import Path

from app.config import get_settings
from app.core.celery_app import celery_app
from app.core.logger import get_logger
from app.core.security import TaskSecurity
from app.schemas.context import AuthType, UserContext
from app.services.capture_processor import CaptureProcessor
from app.services.capture_repository import CaptureRepository
from app.services.line_events import LineEventProcessor

settings = get_settings()
logger = get_logger(__name__)


def capture_owner(context: UserContext) -> str:
    if context.type == AuthType.ADMIN:
        return "owner"
    return "demo:" + hashlib.sha256((context.notion_token or "").encode()).hexdigest()[:24]


@celery_app.task(
    bind=True,
    max_retries=3,
    soft_time_limit=settings.CAPTURE_TASK_TIMEOUT_SECONDS,
    time_limit=settings.CAPTURE_TASK_TIMEOUT_SECONDS + 30,
)
def process_capture(self, capture_id: str):
    repository = CaptureRepository(settings.CAPTURE_DATA_DIR)
    try:
        return CaptureProcessor(repository, settings).process(capture_id)
    except Exception as exc:
        logger.error("Capture processing failed: id=%s type=%s", capture_id, type(exc).__name__)
        raise self.retry(
            exc=RuntimeError("Capture processing failed; inspect capture status"),
            countdown=60 * (2**self.request.retries),
        ) from None


@celery_app.task(bind=True, max_retries=3)
def process_voice_note(self, file_path: str, encrypted_context: str | None = None):
    """舊版佇列相容入口；只允許既有私人 data volume 中的音訊。"""
    path = Path(file_path).resolve()
    path.relative_to(settings.CAPTURE_DATA_DIR.resolve())
    if path.stat().st_size > settings.CAPTURE_MAX_BYTES:
        raise ValueError("Audio exceeds capture limit")
    context = (
        UserContext(**TaskSecurity.decrypt_payload(encrypted_context))
        if encrypted_context
        else UserContext(type=AuthType.ADMIN)
    )
    repository = CaptureRepository(settings.CAPTURE_DATA_DIR)
    record, _ = repository.create(
        owner_id=capture_owner(context),
        channel="siri",
        kind="audio",
        destination="notion" if context.type == AuthType.DEMO else settings.CAPTURE_DESTINATION,
        content=path.read_bytes(),
        original_name=path.name,
        mime_type="audio/mp4",
        source_key="legacy:" + str(path),
        encrypted_context=encrypted_context,
    )
    try:
        result = CaptureProcessor(repository, settings).process(record.id)
        path.unlink(missing_ok=True)
        return result
    except Exception as exc:
        logger.error("Legacy capture failed: id=%s type=%s", record.id, type(exc).__name__)
        raise self.retry(
            exc=RuntimeError("Capture processing failed"), countdown=60 * (2**self.request.retries)
        ) from None


@celery_app.task(
    bind=True,
    max_retries=3,
    soft_time_limit=settings.CAPTURE_TASK_TIMEOUT_SECONDS,
    time_limit=settings.CAPTURE_TASK_TIMEOUT_SECONDS + 30,
)
def process_line_event(self, event_id: str):
    try:
        LineEventProcessor(
            CaptureRepository(settings.CAPTURE_DATA_DIR), settings, process_capture.delay
        ).process(event_id)
    except Exception as exc:
        logger.error("LINE event failed: id=%s type=%s", event_id, type(exc).__name__)
        raise self.retry(
            exc=RuntimeError("LINE event processing failed"),
            countdown=60 * (2**self.request.retries),
        ) from None
