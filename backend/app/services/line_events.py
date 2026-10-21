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
from app.services.line_client import LineClient

EVENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class LineEventStore:
    def __init__(self, repository: CaptureRepository):
        self.repository = repository
        with repository.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS line_events (id TEXT PRIMARY KEY, "
                "payload TEXT, status TEXT NOT NULL DEFAULT 'pending', error_code TEXT)"
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
        encrypted = TaskSecurity.encrypt_payload(event)
        with self.repository.connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO line_events(id,payload) VALUES(?,?)", (event_id, encrypted)
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
            if event["type"] == "postback":
                data = event.get("postback", {}).get("data", "")
                command = "/review " + data[7:] if data.startswith("review:") else "/help"
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
        try:
            if len(parts) == 2 and parts[0] == "/files":
                text = management.describe(parts[1])
            elif len(parts) == 2 and parts[0] == "/review":
                record = management.review(parts[1])
                text = f"{record.id} 已確認完整原文與摘要；尚未清理任何檔案。"
            else:
                text = "管理指令：/files C代號、/review C代號。校對表示已檢查完整原文與摘要。"
        except (ValueError, KeyError):
            text = "找不到可操作的收件，或該收件尚未具備完整原文與摘要。"
        self.client.push(text, key="event:" + event_id)

    def capture(self, message: dict, event_id: str):
        kind, message_id = message.get("type"), message.get("id", "")
        if not isinstance(message_id, str) or not EVENT_ID.fullmatch(message_id):
            raise ValueError("Invalid LINE message ID")
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
        self.enqueue(record.id)
