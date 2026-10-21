"""相容 Siri 上傳與個人文字收件；可靠保存後才送入佇列。"""

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.config import get_settings
from app.core.dependencies import get_personal_context, get_user_context
from app.core.security import TaskSecurity
from app.schemas.context import AuthType, UserContext
from app.schemas.voice_note import VoiceNoteResponse
from app.services.audio_validator import validate_audio_format
from app.services.capture_repository import CaptureRepository
from app.worker.tasks import capture_owner, process_capture

settings = get_settings()
router = APIRouter(prefix="/api/v1", tags=["Capture"])


class TextCaptureInput(BaseModel):
    text: str = Field(min_length=1, max_length=1_000_000)


def enqueue_capture(
    content: bytes,
    filename: str,
    mime_type: str,
    kind: str,
    channel: str,
    context: UserContext,
    stored_extension: str | None = None,
) -> VoiceNoteResponse:
    if not content or len(content) > settings.CAPTURE_MAX_BYTES:
        raise HTTPException(status_code=413 if content else 400, detail="Invalid capture size")
    destination = "notion" if context.type == AuthType.DEMO else settings.CAPTURE_DESTINATION
    if destination == "notion" and not (
        context.notion_token if context.type == AuthType.DEMO else settings.NOTION_TOKEN
    ):
        raise HTTPException(status_code=503, detail="Notion destination is not configured")
    encrypted = (
        TaskSecurity.encrypt_payload(context.model_dump())
        if context.type == AuthType.DEMO
        else None
    )
    repository = CaptureRepository(settings.CAPTURE_DATA_DIR)
    record, _ = repository.create(
        owner_id=capture_owner(context),
        channel=channel,
        kind=kind,
        destination=destination,
        content=content,
        original_name=filename,
        mime_type=mime_type,
        encrypted_context=encrypted,
        stored_extension=stored_extension,
    )
    try:
        task = process_capture.delay(record.id)
    except Exception:
        repository.update_capture(record.id, error_code="QueueUnavailable")
        raise HTTPException(
            status_code=503,
            detail={"message": "Source saved; queue unavailable", "capture_id": record.id},
        ) from None
    return VoiceNoteResponse(message="已收到，開始處理", task_id=task.id, capture_id=record.id)


@router.post("/note", response_model=VoiceNoteResponse, status_code=202)
async def upload_voice_note(
    audio: UploadFile = File(...), context: UserContext = Depends(get_user_context)
):
    content = await audio.read(settings.CAPTURE_MAX_BYTES + 1)
    if len(content) > settings.CAPTURE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Audio exceeds capture limit")
    extension = validate_audio_format(content)
    return enqueue_capture(
        content,
        audio.filename or "original" + extension,
        "audio/" + extension[1:],
        "audio",
        "http",
        context,
        stored_extension=extension,
    )


@router.post("/note/ios", response_model=VoiceNoteResponse, status_code=202)
async def upload_voice_note_ios(request: Request, context: UserContext = Depends(get_user_context)):
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > settings.CAPTURE_MAX_BYTES:
            raise HTTPException(status_code=413, detail="Audio exceeds capture limit")
        content.extend(chunk)
    extension = validate_audio_format(bytes(content))
    return enqueue_capture(
        bytes(content), "original" + extension, "audio/" + extension[1:], "audio", "siri", context
    )


@router.post("/captures/text", response_model=VoiceNoteResponse, status_code=202)
async def capture_text(
    payload: TextCaptureInput, context: UserContext = Depends(get_personal_context)
):
    if not payload.text.strip() or len(payload.text) > settings.CAPTURE_MAX_TEXT_CHARS:
        raise HTTPException(status_code=400, detail="Text is empty or exceeds the configured limit")
    return enqueue_capture(
        payload.text.encode(), "original.txt", "text/plain", "text", "http", context
    )
