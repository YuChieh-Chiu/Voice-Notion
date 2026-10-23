"""真實路由／SQLite／處理器串接，僅在外部服務邊界使用離線替身。"""

from unittest.mock import MagicMock

import pytest
from drive_fakes import FakeDrive
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.routes import capture_management, voice_note
from app.services.capture_processor import CaptureProcessor
from app.services.capture_repository import CaptureRepository
from app.services.deletion_service import DeletionService
from app.services.drive_storage import DriveStorage
from app.services.line_events import LineEventProcessor, LineEventStore


@pytest.mark.parametrize("kind", ["text", "audio", "image", "file"])
def test_line_event_to_drive_then_confirmed_full_cleanup(tmp_path, kind):
    config = Settings(
        _env_file=None,
        CAPTURE_DATA_DIR=tmp_path,
        CAPTURE_DESTINATION="drive",
        DRIVE_FOLDER_ID="root",
    )
    repository = CaptureRepository(tmp_path)
    fake_drive = FakeDrive()
    storage = DriveStorage(repository, config, service=fake_drive)
    line, model, stt = MagicMock(), MagicMock(), MagicMock()
    line.download.return_value = b"RIFF0000WAVEbytes" if kind == "audio" else b"%PDF-1.7 bytes"
    model.summarize.return_value, stt.transcribe.return_value = "summary", "complete transcript"
    worker = CaptureProcessor(
        repository,
        config,
        drive_factory=lambda *args: storage,
        llm_factory=lambda **kwargs: model,
        stt_factory=lambda: stt,
        notifier_factory=lambda **kwargs: MagicMock(),
    )
    event = {
        "webhookEventId": "integration",
        "type": "message",
        "source": {"type": "user", "userId": "test-owner"},
        "message": {"id": "message", "type": kind, "text": "full\noriginal"},
    }
    events = LineEventStore(repository)
    events.accept(event)
    LineEventProcessor(repository, config, worker.process, client=line).process("integration")
    record = repository.list_captures("owner")[0]
    assert record.status == "complete" and events.load("integration")["payload"] is None
    assert len(repository.files(record.id)) == {"text": 2, "audio": 3, "image": 1, "file": 1}[kind]
    if kind in ("image", "file"):
        model.summarize.assert_not_called()
        stt.transcribe.assert_not_called()
    deletion = DeletionService(repository, config, drive_factory=lambda *args: storage)
    preview = deletion.preview("all", capture_id=record.id)
    assert not any(op == "trash" for op, _ in fake_drive.operations)
    assert deletion.confirm(preview["token"])["complete"]
    assert worker.process(record.id)["status"] == "removed"
    assert repository.get(record.id).status == "deleted"
    assert not list(repository.directory(record.id).iterdir())


def test_http_source_url_is_metadata_only_and_management_uses_same_files(tmp_path, monkeypatch):
    config = Settings(
        _env_file=None,
        CAPTURE_DATA_DIR=tmp_path,
        CAPTURE_DESTINATION="drive",
        DRIVE_FOLDER_ID="root",
    )
    monkeypatch.setattr(voice_note, "settings", config)
    monkeypatch.setattr(capture_management, "settings", config)
    queue = MagicMock(return_value=MagicMock(id="test-task"))
    monkeypatch.setattr(voice_note.process_capture, "delay", queue)
    http = TestClient(app, base_url="http://localhost")
    headers = {"X-API-Key": "test-personal"}
    response = http.post(
        "/api/v1/captures/text",
        headers=headers,
        json={"text": "完整原文", "source_url": "https://example.org/source"},
    )
    assert response.status_code == 202
    repository = CaptureRepository(tmp_path)
    record = repository.get(response.json()["capture_id"])
    assert record.source_url == "https://example.org/source"
    assert queue.call_args.args == (record.id,)
    storage = DriveStorage(repository, config, service=FakeDrive())
    model = MagicMock()
    model.summarize.return_value = "摘要"
    CaptureProcessor(
        repository,
        config,
        drive_factory=lambda *args: storage,
        llm_factory=lambda **kwargs: model,
        notifier_factory=lambda **kwargs: MagicMock(),
    ).process(record.id)
    note = next(f for f in repository.files(record.id) if f.role == "note")
    assert "https://example.org/source" in storage.read_cloud_file(note).decode()
    data = http.get("/api/v1/captures/" + record.id, headers=headers).json()
    assert len(data["files"]) == 2 and "encrypted_context" not in data["capture"]
    for url in (
        "file:///etc/passwd",
        "https://test-user:test-password@example.invalid/",
        "http://",
    ):
        assert (
            http.post(
                "/api/v1/captures/text", headers=headers, json={"text": "text", "source_url": url}
            ).status_code
            == 422
        )


def test_explicit_batch_selects_only_owned_file_ids_across_captures(tmp_path):
    repository = CaptureRepository(tmp_path)
    config = Settings(_env_file=None, DRIVE_FOLDER_ID="root")
    drive = FakeDrive()
    storage = DriveStorage(repository, config, service=drive)
    service = DeletionService(repository, config, drive_factory=lambda *args: storage)
    files = []
    for owner in ("owner", "owner", "demo:other"):
        record, _ = repository.create(
            owner_id=owner,
            channel="http",
            kind="file",
            destination="drive",
            content=b"%PDF-raw",
            original_name="file.pdf",
            mime_type="application/pdf",
        )
        files.append(storage.upload(repository.files(record.id)[0]))
    with pytest.raises(KeyError):
        service.preview("files", file_ids=[files[0].id, files[2].id])
    preview = service.preview("files", file_ids=[files[0].id, files[1].id, files[1].id])
    assert preview["count"] == 2
    assert service.confirm(preview["token"])["complete"]
    assert (
        drive.entries[files[0].cloud_id]["trashed"] and drive.entries[files[1].cloud_id]["trashed"]
    )
    assert not drive.entries[files[2].cloud_id]["trashed"]


def test_celery_task_results_never_contain_original_titles_or_urls(tmp_path, monkeypatch):
    from app.worker import tasks

    config = Settings(_env_file=None, CAPTURE_DATA_DIR=tmp_path)
    monkeypatch.setattr(tasks, "settings", config)
    processor = MagicMock()
    processor.process.return_value = {
        "status": "success",
        "title": "private original first line",
        "url": "https://notion.so/private-page",
    }
    monkeypatch.setattr(tasks, "CaptureProcessor", lambda *args: processor)
    assert tasks.process_capture.run("C0123456789abcdef") == {
        "status": "success",
        "capture_id": "C0123456789abcdef",
    }
    path = tmp_path / "upload.m4a"
    path.write_bytes(b"RIFF0000WAVEbytes")
    result = tasks.process_voice_note.run(str(path))
    assert set(result) == {"status", "capture_id"}
    assert "private" not in str(result)


def test_notion_retraction_preserves_children_added_under_a_capture_block(tmp_path):
    from app.services.notion_capture_storage import (
        NotionCaptureStorage,
        block_fingerprint,
        text_block,
    )

    repository = CaptureRepository(tmp_path)
    record, _ = repository.create(
        owner_id="owner",
        channel="line",
        kind="text",
        destination="notion",
        content=b"full",
        original_name="text",
        mime_type="text/plain",
    )
    owned = dict(text_block("owned text"), id="owned", has_children=True)
    repository.save_checkpoint(record.id, "notion_page", {"id": "shared-page"})
    repository.save_checkpoint(
        record.id, "notion_parts", {"0": {"ids": ["owned"], "hashes": [block_fingerprint(owned)]}}
    )
    notion = MagicMock()
    notion.client.blocks.children.list.return_value = {"results": [owned], "has_more": False}
    with pytest.raises(RuntimeError, match="externally added children"):
        NotionCaptureStorage(repository, notion).retract(record.id)
    notion.client.blocks.delete.assert_not_called()
