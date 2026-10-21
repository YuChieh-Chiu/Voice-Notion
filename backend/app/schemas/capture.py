"""收件與內容檔案的穩定資料格式。"""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

CaptureKind = Literal["text", "audio", "image", "file"]
CaptureChannel = Literal["siri", "http", "line"]
FileRole = Literal["original", "transcript", "note"]
PERSONAL_OWNER = "owner"


def validate_source_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if (
        len(value) > 2048
        or parsed.scheme not in ("https", "http")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Invalid source URL")
    return value


class CaptureRecord(BaseModel):
    id: str
    owner_id: str
    source_key: str
    channel: CaptureChannel
    kind: CaptureKind
    destination: Literal["notion", "drive"]
    original_name: str
    created_at: str
    source_url: str | None = None
    status: str = "pending"
    category: str | None = None
    reviewed_at: str | None = None
    folder_id: str | None = None
    notion_url: str | None = None
    encrypted_context: str | None = Field(default=None, exclude=True)
    error_code: str | None = None
    notified: bool = False
    revision: int = 0


class CaptureFile(BaseModel):
    id: str
    capture_id: str
    role: FileRole
    name: str
    mime_type: str
    size_bytes: int
    sha256: str
    cloud_id: str | None = None
    cloud_url: str | None = None
    state: str = "local"
    revision: int = 0
