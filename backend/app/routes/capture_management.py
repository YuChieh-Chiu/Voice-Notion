"""部署者專用收件與清理 API；BYOK 無法進入此範圍。"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import get_settings
from app.core.dependencies import get_personal_context
from app.services.capture_repository import CaptureRepository
from app.services.deletion_service import DeletionConflict, DeletionService
from app.worker.tasks import process_capture

settings = get_settings()
router = APIRouter(
    prefix="/api/v1", tags=["Capture management"], dependencies=[Depends(get_personal_context)]
)


class DeletionPreviewInput(BaseModel):
    scope: Literal["original", "file", "files", "all", "reviewed"]
    capture_id: str | None = Field(default=None, pattern=r"^C[0-9a-f]{16}$")
    file_id: str | None = Field(default=None, pattern=r"^F[0-9a-f]{16}$")
    file_ids: list[str] | None = Field(default=None, min_length=1, max_length=500)
    category: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    before: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class ConfirmationInput(BaseModel):
    token: str = Field(min_length=20, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


def repository():
    return CaptureRepository(settings.CAPTURE_DATA_DIR)


def execute(operation):
    try:
        return operation()
    except KeyError:
        raise HTTPException(404, "Capture or confirmation not found") from None
    except DeletionConflict:
        raise HTTPException(409, "State changed or confirmation expired; preview again") from None
    except ValueError:
        raise HTTPException(
            400, "Operation is not eligible; specify a valid capture, file and scope"
        ) from None
    except Exception:
        raise HTTPException(
            502, "Operation incomplete; saved state is available for retry"
        ) from None


@router.get("/captures")
def list_captures(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    return execute(
        lambda: [r.model_dump() for r in repository().list_captures("owner", limit, offset)]
    )


@router.get("/captures/{capture_id}")
def get_capture(capture_id: str):
    def read():
        store = repository()
        return {
            "capture": store.get(capture_id, "owner").model_dump(),
            "files": [f.model_dump() for f in store.files(capture_id)],
        }

    return execute(read)


@router.post("/captures/{capture_id}/review")
def review(capture_id: str):
    return execute(lambda: DeletionService(repository(), settings).review(capture_id).model_dump())


@router.post("/captures/{capture_id}/retry", status_code=202)
def retry(capture_id: str):
    def enqueue():
        record = repository().get(capture_id, "owner")
        if record.status in ("delete_pending", "deleted"):
            raise DeletionConflict("Capture removed")
        task = process_capture.delay(capture_id)
        return {"capture_id": capture_id, "task_id": task.id}

    return execute(enqueue)


@router.post("/deletions/preview")
def preview(payload: DeletionPreviewInput):
    return execute(lambda: DeletionService(repository(), settings).preview(**payload.model_dump()))


@router.post("/deletions/confirm")
def confirm(payload: ConfirmationInput):
    return execute(lambda: DeletionService(repository(), settings).confirm(payload.token))


@router.post("/deletions/cancel")
def cancel(payload: ConfirmationInput):
    return execute(lambda: DeletionService(repository(), settings).cancel(payload.token))
