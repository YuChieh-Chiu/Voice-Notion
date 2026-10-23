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
from app.services.deletion_service import DeletionService
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


@pytest.mark.parametrize("command", ["/help", "/"])
def test_help_documents_commands_without_capturing_or_deleting(line_setup, command):
    settings, repository, _ = line_setup
    record, _ = repository.create(
        owner_id="owner",
        channel="line",
        kind="file",
        destination="drive",
        content=b"preserved",
        original_name="file.pdf",
        mime_type="application/pdf",
    )
    store = LineEventStore(repository)
    store.accept(event(text=command))
    client, enqueue = MagicMock(), MagicMock()
    LineEventProcessor(repository, settings, enqueue, client=client).process("event1")
    text = client.push.call_args.args[0]
    for syntax in (
        "/help",
        "/files C",
        "/review C",
        "/clear C",
        " original",
        " all",
        "/clear file F",
        "/clear reviewed",
        "topic=",
        "before=",
        "UTC",
        "/confirm token",
        "/cancel token",
        "人工校對",
        "垃圾桶",
        "仍占",
    ):
        assert syntax in text
    assert len(text) < 4400 and len(text.splitlines()) > 10
    assert [r.id for r in repository.list_captures("owner")] == [record.id]
    original = repository.files(record.id)[0]
    assert original.state == "local" and repository.read_file(original) == b"preserved"
    enqueue.assert_not_called()
    client.download.assert_not_called()


@pytest.mark.parametrize("action", ["cancel", "expired", "confirm"])
def test_preview_text_tokens_match_buttons_and_use_existing_confirmation_guards(line_setup, action):
    settings, repository, _ = line_setup
    record, _ = repository.create(
        owner_id="owner",
        channel="line",
        kind="file",
        destination="drive",
        content=b"preserved",
        original_name="file.pdf",
        mime_type="application/pdf",
    )
    client = MagicMock()
    processor = LineEventProcessor(repository, settings, MagicMock(), client=client)
    processor.command("/clear " + record.id + " all", "preview")
    text = client.push.call_args.args[0]
    token = next(line.split()[1] for line in text.splitlines() if line.startswith("/confirm "))
    assert "/cancel " + token in text
    assert [item["data"] for item in client.push.call_args.kwargs["actions"]] == [
        "confirm:" + token,
        "cancel:" + token,
    ]
    service = DeletionService(repository, settings)
    with pytest.raises(KeyError):
        service.confirm(token, owner_id="someone-else")
    original = repository.files(record.id)[0]
    assert repository.read_file(original) == b"preserved"
    if action == "cancel":
        processor.command("/cancel " + token, "cancel")
        processor.command("/confirm " + token, "confirm-cancelled")
        assert "已取消" in client.push.call_args.args[0]
    elif action == "expired":
        with repository.connection() as connection:
            connection.execute("UPDATE deletion_previews SET expires=0")
        processor.command("/confirm " + token, "confirm-expired")
        assert "過期" in client.push.call_args.args[0]
    else:
        processor.command("/confirm " + token, "confirm")
        assert repository.get(record.id).status == "deleted"
        processor.command("/confirm " + token, "confirm-replayed")
        assert "清理完成" in client.push.call_args.args[0]
    if action != "confirm":
        assert repository.files(record.id)[0].state == "local"
        assert repository.read_file(original) == b"preserved"


def test_long_preview_including_text_fallback_is_cancelled(line_setup, monkeypatch):
    settings, repository, _ = line_setup
    deletion = MagicMock()
    deletion.preview.return_value = {
        "token": "test-preview-token",
        "count": 1,
        "total_bytes": 7,
        "files": [
            {"capture_id": "C" + "a" * 16, "id": "F" + "b" * 16, "role": "original", "bytes": 7}
        ],
        "warning": "說" * 4260,
    }
    monkeypatch.setattr("app.services.line_events.DeletionService", lambda *args: deletion)
    client = MagicMock()
    processor = LineEventProcessor(repository, settings, MagicMock(), client=client)
    processor.command("/clear C" + "a" * 16 + " all", "long-preview")
    deletion.cancel.assert_called_once_with("test-preview-token")
    deletion.confirm.assert_not_called()
    assert "清單太長" in client.push.call_args.args[0]
    assert client.push.call_args.kwargs["actions"] == []


def capture_with_artifacts(
    repository, kind="audio", *, owner_id="owner", status="complete", reviewed=False, omitted=()
):
    record, _ = repository.create(
        owner_id=owner_id,
        channel="line",
        kind=kind,
        destination="drive",
        content=b"preserved",
        original_name="source",
        mime_type="application/octet-stream",
    )
    if kind in ("text", "audio") and "note" not in omitted:
        repository.save_artifact(record.id, "note", b"summary")
    if kind == "audio" and "transcript" not in omitted:
        repository.save_artifact(record.id, "transcript", b"full transcript")
    for file in repository.files(record.id):
        repository.update_file(file.id, state="uploaded")
    repository.update_capture(
        record.id,
        status=status,
        reviewed_at="2026-10-23T00:00:00+00:00" if reviewed else None,
    )
    return repository.get(record.id)


@pytest.mark.parametrize(
    "kind,status,reviewed,omitted,removed_role,expected",
    [
        ("text", "complete", False, (), None, "review:"),
        ("text", "complete", True, (), None, "review:"),
        ("audio", "complete", False, (), None, "review:"),
        ("audio", "complete", True, (), None, "clear-original:"),
        ("file", "complete", False, (), None, None),
        ("image", "complete", False, (), None, None),
        ("audio", "failed", False, (), None, None),
        ("audio", "deleted", False, (), None, None),
        ("audio", "complete", False, ("transcript",), None, None),
        ("text", "complete", False, ("note",), None, None),
        ("audio", "complete", True, (), "transcript", None),
    ],
)
def test_files_restores_only_eligible_source_actions_without_changing_content(
    line_setup, kind, status, reviewed, omitted, removed_role, expected
):
    settings, repository, _ = line_setup
    record = capture_with_artifacts(
        repository, kind, status=status, reviewed=reviewed, omitted=omitted
    )
    if removed_role:
        removed = next(file for file in repository.files(record.id) if file.role == removed_role)
        repository.update_file(removed.id, state="trashed")
    before_record = repository.get(record.id).model_dump()
    before_files = [file.model_dump() for file in repository.files(record.id)]
    client, enqueue = MagicMock(), MagicMock()
    LineEventProcessor(repository, settings, enqueue, client=client).command(
        "/files " + record.id, "files"
    )
    actions = client.push.call_args.kwargs["actions"]
    assert [action["data"] for action in actions] == ([expected + record.id] if expected else [])
    assert all(action["type"] == "postback" for action in actions)
    assert repository.get(record.id).model_dump() == before_record
    assert [file.model_dump() for file in repository.files(record.id)] == before_files
    original = next(file for file in repository.files(record.id) if file.role == "original")
    assert repository.read_file(original) == b"preserved"
    enqueue.assert_not_called()


def test_files_actions_use_requested_capture_and_reject_other_owner(line_setup):
    settings, repository, _ = line_setup
    first = capture_with_artifacts(repository, "text")
    requested = capture_with_artifacts(repository, "audio", reviewed=True)
    foreign = capture_with_artifacts(repository, "audio", owner_id="other-owner")
    client = MagicMock()
    processor = LineEventProcessor(repository, settings, MagicMock(), client=client)
    processor.command("/files " + requested.id, "requested")
    assert [action["data"] for action in client.push.call_args.kwargs["actions"]] == [
        "clear-original:" + requested.id
    ]
    assert first.id not in client.push.call_args.args[0]
    processor.command("/files " + foreign.id, "foreign")
    assert client.push.call_args.kwargs["actions"] == []
    assert foreign.id not in client.push.call_args.args[0]
    assert "找不到" in client.push.call_args.args[0]
    assert repository.get(foreign.id).status == "complete"


def test_file_alias_has_identical_results_actions_and_owner_scope(line_setup):
    settings, repository, _ = line_setup
    record = capture_with_artifacts(repository, "audio", reviewed=True)
    foreign = capture_with_artifacts(repository, "audio", owner_id="other-owner")
    client = MagicMock()
    processor = LineEventProcessor(repository, settings, MagicMock(), client=client)
    processor.command("/files " + record.id, "plural")
    plural = client.push.call_args
    processor.command("/file " + record.id, "alias")
    alias = client.push.call_args
    assert alias.args[0] == plural.args[0]
    assert alias.kwargs["actions"] == plural.kwargs["actions"]
    assert alias.kwargs["actions"][0]["data"] == "clear-original:" + record.id
    processor.command("/file " + foreign.id, "alias-foreign")
    assert "找不到" in client.push.call_args.args[0]
    assert foreign.id not in client.push.call_args.args[0]
    assert client.push.call_args.kwargs["actions"] == []
    assert repository.get(foreign.id).status == "complete"


@pytest.mark.parametrize(
    "command,expected",
    [
        ("/unknown-command", "無法辨識指令"),
        ("/help extra", "無法辨識指令"),
        ("/files", "用法：/files C代號"),
        ("/file", "用法：/files C代號"),
    ],
)
def test_unknown_and_incomplete_commands_give_short_guidance_without_mutating_captures(
    line_setup, command, expected
):
    settings, repository, _ = line_setup
    record = capture_with_artifacts(repository, "text")
    before = repository.get(record.id).model_dump()
    files_before = [file.model_dump() for file in repository.files(record.id)]
    store = LineEventStore(repository)
    store.accept(event(text=command))
    client, enqueue = MagicMock(), MagicMock()
    LineEventProcessor(repository, settings, enqueue, client=client).process("event1")
    assert expected in client.push.call_args.args[0]
    assert len(client.push.call_args.args[0]) < 100
    assert client.push.call_args.kwargs["actions"] == []
    assert [r.id for r in repository.list_captures("owner")] == [record.id]
    assert repository.get(record.id).model_dump() == before
    assert [file.model_dump() for file in repository.files(record.id)] == files_before
    original = next(file for file in repository.files(record.id) if file.role == "original")
    assert repository.read_file(original) == b"preserved"
    enqueue.assert_not_called()
    client.download.assert_not_called()
