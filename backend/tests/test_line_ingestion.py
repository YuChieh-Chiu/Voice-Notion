import base64
import hashlib
import hmac
import json
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.security import TaskSecurity
from app.main import app
from app.routes import line_webhook
from app.services.capture_management import CaptureManagement
from app.services.capture_repository import CaptureRepository
from app.services.line_client import LineClient
from app.services.line_events import LineEventProcessor, LineEventStore
from app.services.notification_service import NotificationService


@pytest.fixture
def line_setup(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        CAPTURE_DATA_DIR=tmp_path,
        CAPTURE_DESTINATION="drive",
        CAPTURE_MAX_BYTES=1024,
    )
    monkeypatch.setattr(line_webhook, "settings", settings)
    queue = MagicMock()
    monkeypatch.setattr(line_webhook.process_line_event, "delay", queue)
    return settings, CaptureRepository(tmp_path), queue


def event(kind="text", **message):
    return {
        "type": "message",
        "webhookEventId": "event1",
        "source": {"type": "user", "userId": "test-owner"},
        "message": {"id": "message1", "type": kind, "text": "完整原始訊息", **message},
    }


def post(payload, signature=True):
    body = json.dumps(payload, ensure_ascii=False).encode()
    signature = (
        base64.b64encode(hmac.digest(b"test-channel", body, hashlib.sha256)).decode()
        if signature
        else "invalid"
    )
    return TestClient(app, base_url="http://localhost").post(
        "/api/v1/line/webhook", content=body, headers={"x-line-signature": signature}
    )


def test_signature_whitelist_private_chat_and_encrypted_outbox(line_setup):
    _, repository, queue = line_setup
    assert post({"events": [event()]}, False).status_code == 401
    wrong = event()
    wrong["source"]["userId"] = "someone-else"
    group = event()
    group["source"]["type"] = "group"
    assert post({"events": [wrong, group]}).status_code == 200
    queue.assert_not_called()
    assert post({"events": [event()]}).status_code == 200
    queue.assert_called_once_with("event1")
    stored = LineEventStore(repository).load("event1")
    assert "完整原始訊息" not in stored["payload"]
    assert TaskSecurity.decrypt_payload(stored["payload"]) == event()


@pytest.mark.parametrize(
    "kind,content,count",
    [
        ("text", b"", 2),
        ("audio", b"RIFF0000WAVEdata", 3),
        ("image", b"\x89PNG\r\n\x1a\nraw", 1),
        ("file", b"%PDF-1.7 raw", 1),
    ],
)
def test_durable_ingestion_download_and_redelivery(line_setup, kind, content, count):
    settings, repository, _ = line_setup
    store = LineEventStore(repository)
    store.accept(event(kind, fileName="../../report.pdf"))
    client, enqueue = MagicMock(), MagicMock()
    client.download.return_value = content
    processor = LineEventProcessor(repository, settings, enqueue, client=client)
    processor.process("event1")
    processor.process("event1")
    records = repository.list_captures("owner")
    assert len(records) == 1
    original = repository.files(records[0].id)[0]
    assert repository.read_file(original) == (
        "完整原始訊息".encode() if kind == "text" else content
    )
    enqueue.assert_called_once_with(records[0].id)
    assert store.load("event1")["payload"] is None
    if kind == "text":
        client.download.assert_not_called()


def test_queue_failure_keeps_source_and_redelivery_reuses_it(line_setup):
    settings, repository, _ = line_setup
    store = LineEventStore(repository)
    store.accept(event())
    enqueue = MagicMock(side_effect=[RuntimeError("offline"), None])
    processor = LineEventProcessor(repository, settings, enqueue, client=MagicMock())
    with pytest.raises(RuntimeError):
        processor.process("event1")
    assert len(repository.list_captures("owner")) == 1
    processor.process("event1")
    assert enqueue.call_args_list[0] == enqueue.call_args_list[1]


def test_notion_rejects_attachment_without_downloading_or_fallback(line_setup):
    settings, repository, _ = line_setup
    settings.CAPTURE_DESTINATION = "notion"
    LineEventStore(repository).accept(event("file"))
    client = MagicMock()
    LineEventProcessor(repository, settings, MagicMock(), client=client).process("event1")
    client.download.assert_not_called()
    assert not repository.list_captures("owner")


def test_review_requires_complete_text_and_never_deletes(line_setup):
    _, repository, _ = line_setup
    record, _ = repository.create(
        owner_id="owner",
        channel="line",
        kind="text",
        destination="drive",
        content=b"full",
        original_name="text.txt",
        mime_type="text/plain",
    )
    service = CaptureManagement(repository)
    with pytest.raises(ValueError):
        service.review(record.id)
    repository.update_capture(record.id, status="complete")
    assert service.review(record.id).reviewed_at
    assert repository.read_file(repository.files(record.id)[0]) == b"full"
    with pytest.raises(KeyError):
        service.review(record.id, "other-owner")


@pytest.mark.parametrize(
    "status,headers,body",
    [
        (302, {"location": "https://evil.example"}, b""),
        (200, {"content-length": "1025"}, b"big"),
        (200, {}, b"x" * 1025),
    ],
)
def test_download_rejects_redirect_or_oversize(line_setup, status, headers, body):
    settings, _, _ = line_setup
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, headers=headers, content=body)

    client = LineClient(settings, transport=httpx.MockTransport(respond))
    with pytest.raises((ValueError, httpx.HTTPStatusError)):
        client.download("safe-message")
    assert len(requests) == 1 and requests[0].url.host == "api-data.line.me"
    with pytest.raises(ValueError):
        client.download("../escape")


def test_push_has_stable_retry_key_and_accepts_already_delivered(line_setup):
    settings, _, _ = line_setup
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(409, headers={"x-line-accepted-request-id": "offline"})

    client = LineClient(settings, transport=httpx.MockTransport(respond))
    client.push("通知", key="capture1")
    client.push("通知", key="capture1")
    assert requests[0].headers["x-line-retry-key"] == requests[1].headers["x-line-retry-key"]


def test_partial_and_complete_notifications_have_distinct_keys(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr("app.services.notification_service.LineClient", lambda config: client)
    service = NotificationService()
    service.push_partial("C123")
    service.push_message("title", "https://drive.google.com/", capture_id="C123")
    assert [c.kwargs["key"] for c in client.push.call_args_list] == [
        "partial:C123",
        "complete:C123",
    ]


def test_debug_mode_requires_shared_persistent_encryption_key(monkeypatch):
    monkeypatch.setattr(TaskSecurity, "_fernet", None)
    monkeypatch.setattr(
        "app.core.security.settings", Settings(_env_file=None, DEBUG=True, TASK_ENCRYPTION_KEY="")
    )
    with pytest.raises(ValueError, match="web and worker"):
        TaskSecurity.encrypt_payload({"text": "private"})
