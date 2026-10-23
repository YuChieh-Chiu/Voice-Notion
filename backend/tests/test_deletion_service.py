import copy
import threading
from unittest.mock import MagicMock

import pytest
from drive_fakes import DriveCall, FakeDrive
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.routes import capture_management
from app.services.capture_processor import CaptureProcessor
from app.services.capture_repository import CaptureRepository
from app.services.deletion_service import DeletionConflict, DeletionService
from app.services.drive_storage import DriveStorage
from app.services.line_events import LineEventProcessor, LineEventStore
from app.services.notion_capture_storage import NotionCaptureStorage


@pytest.fixture
def setup(tmp_path):
    repository = CaptureRepository(tmp_path)
    settings = Settings(
        _env_file=None,
        CAPTURE_DATA_DIR=tmp_path,
        CAPTURE_DESTINATION="drive",
        DRIVE_FOLDER_ID="root",
        CAPTURE_CATEGORIES=[{"id": "fitness", "description": "運動"}],
    )
    fake = FakeDrive()
    storage = DriveStorage(repository, settings, service=fake)
    deletion = DeletionService(repository, settings, drive_factory=lambda *args: storage)
    return repository, settings, fake, storage, deletion


def make_capture(setup, kind="audio", key=None):
    repository, settings, fake, storage, _ = setup
    record, _ = repository.create(
        owner_id="owner",
        channel="line",
        kind=kind,
        destination="drive",
        content=b"original bytes",
        original_name="received.m4a",
        mime_type="audio/mp4",
        source_key=key,
    )
    llm, stt = MagicMock(), MagicMock()
    llm.summarize.return_value, stt.transcribe.return_value = "摘要", "完整未潤飾逐字稿"
    processor = CaptureProcessor(
        repository,
        settings,
        drive_factory=lambda *args: storage,
        llm_factory=lambda **kwargs: llm,
        stt_factory=lambda: stt,
        notifier_factory=lambda **kwargs: MagicMock(),
    )
    processor.process(record.id)
    return repository.get(record.id), processor


def test_reviewed_original_cleanup_keeps_text_updates_links_and_replay_is_safe(setup):
    repository, _, fake, _, service = setup
    record, _ = make_capture(setup)
    with pytest.raises(ValueError):
        service.preview("original", capture_id=record.id)
    service.review(record.id)
    preview = service.preview("original", capture_id=record.id)
    assert preview["count"] == 1 and preview["files"][0]["role"] == "original"
    with pytest.raises(KeyError):
        service.confirm(preview["token"], "other")
    result = service.confirm(preview["token"])
    assert result["complete"]
    files = {f.role: f for f in repository.files(record.id)}
    assert (
        files["original"].state == "trashed"
        and files["note"].state == files["transcript"].state == "uploaded"
    )
    note = fake.content[files["note"].cloud_id].decode()
    assert (
        'original_status: "trashed"' in note
        and files["original"].cloud_id not in note.split("## 原始來源")[1]
    )
    assert "完整原文" in note and "摘要" in note
    operations = list(fake.operations)
    assert service.confirm(preview["token"]) == result and fake.operations == operations
    assert not list(repository.directory(record.id).iterdir())


def test_cancel_expiry_state_and_external_change_require_new_preview(setup, monkeypatch):
    repository, _, fake, _, service = setup
    record, _ = make_capture(setup, "file")
    preview = service.preview("all", capture_id=record.id)
    service.cancel(preview["token"])
    with pytest.raises(DeletionConflict):
        service.confirm(preview["token"])
    preview = service.preview("all", capture_id=record.id)
    monkeypatch.setattr(
        "app.services.deletion_service.time.time", lambda: preview["expires_at"] + 1
    )
    with pytest.raises(DeletionConflict):
        service.confirm(preview["token"])
    monkeypatch.undo()
    preview = service.preview("all", capture_id=record.id)
    repository.update_capture(record.id, category="tech")
    with pytest.raises(DeletionConflict):
        service.confirm(preview["token"])
    preview = service.preview("all", capture_id=record.id)
    file = repository.files(record.id)[0]
    fake.entries[file.cloud_id]["version"] = "changed"
    with pytest.raises(DeletionConflict):
        service.confirm(preview["token"])
    assert not fake.entries[file.cloud_id]["trashed"]


def test_explicit_file_can_remove_only_copy_and_never_recreate(setup):
    repository, _, fake, storage, service = setup
    record, processor = make_capture(setup, "file")
    with pytest.raises(ValueError):
        service.preview("original", capture_id=record.id)
    file = repository.files(record.id)[0]
    preview = service.preview("file", file_id=file.id)
    assert service.confirm(preview["token"])["complete"]
    processor.process(record.id)
    with pytest.raises(ValueError):
        storage.upload(file)
    assert fake.entries[file.cloud_id]["trashed"]


def test_full_cleanup_tombstone_and_failed_subset_retry(setup):
    repository, _, fake, _, service = setup
    record, processor = make_capture(setup)
    preview = service.preview("all", capture_id=record.id)
    original_update = fake.update
    failure = [True]
    failed_id = repository.files(record.id)[0].cloud_id

    def update(fileId, **kwargs):
        if fileId == failed_id and kwargs.get("body", {}).get("trashed") and failure:
            failure.pop()
            return DriveCall(lambda: (_ for _ in ()).throw(PermissionError("offline denied")))
        return original_update(fileId, **kwargs)

    fake.update = update
    first = service.confirm(preview["token"])
    assert not first["complete"] and repository.get(record.id).status == "delete_pending"
    assert service.confirm(preview["token"])["complete"]
    assert (
        repository.get(record.id).status == "deleted"
        and not repository.get(record.id).original_name
    )
    assert processor.process(record.id)["status"] == "removed"
    assert (
        repository.create(
            owner_id="owner",
            channel="line",
            kind="audio",
            destination="drive",
            content=b"new",
            original_name="source",
            mime_type="audio/mp4",
            source_key=record.source_key,
        )[1]
        is False
    )
    assert all(fake.entries[f.cloud_id]["trashed"] for f in repository.files(record.id))


def test_batch_only_reviewed_audio_and_topic_filter(setup):
    repository, _, _, _, service = setup
    first, _ = make_capture(setup)
    second, _ = make_capture(setup)
    image, _ = make_capture(setup, "image")
    service.review(first.id)
    repository.update_capture(first.id, category="fitness")
    preview = service.preview("reviewed", category="fitness", before="2099-01-01")
    assert [f["capture_id"] for f in preview["files"]] == [first.id]
    assert service.confirm(preview["token"])["complete"]
    assert repository.files(second.id)[0].state == repository.files(image.id)[0].state == "uploaded"


def test_intent_is_persisted_before_waiting_for_active_worker(setup):
    repository, _, _, _, service = setup
    record, _ = make_capture(setup, "file")
    preview = service.preview("all", capture_id=record.id)
    started = threading.Event()
    original_execute = service._execute
    service._execute = lambda data: (started.set(), original_execute(data))[1]
    with repository.capture_lock(record.id):
        thread = threading.Thread(target=lambda: service.confirm(preview["token"]))
        thread.start()
        assert started.wait(2)
        assert repository.get(record.id).status == "delete_pending"
        with pytest.raises(ValueError):
            repository.update_capture(record.id, status="complete")
        with pytest.raises(ValueError):
            repository.save_artifact(record.id, "note", b"resurrect")
    thread.join(3)
    assert not thread.is_alive() and repository.get(record.id).status == "deleted"


def test_unsend_before_download_prevents_late_capture(setup):
    repository, settings, _, _, _ = setup
    store = LineEventStore(repository)
    event = {
        "webhookEventId": "late",
        "type": "message",
        "source": {"type": "user", "userId": "test-owner"},
        "message": {"type": "file", "id": "message1"},
    }
    store.accept(event)
    client = MagicMock()

    def download(message_id):
        store.revoke(message_id)
        return b"%PDF-1.7 raw"

    client.download.side_effect = download
    enqueue = MagicMock()
    LineEventProcessor(repository, settings, enqueue, client=client).process("late")
    enqueue.assert_not_called()
    assert all(r.status == "deleted" for r in repository.list_captures("owner"))
    assert store.accept(dict(event, webhookEventId="late-redelivery")) is None


def test_notion_retraction_deletes_only_owned_blocks_and_is_recoverable(setup):
    repository, _, _, _, _ = setup
    record, _ = repository.create(
        owner_id="owner",
        channel="line",
        kind="text",
        destination="notion",
        content=b"full",
        original_name="text",
        mime_type="text/plain",
    )
    notion = MagicMock()
    notion.client.pages.retrieve.return_value = {"id": "page", "url": "https://notion.so/page"}
    blocks = [
        {
            "id": "unrelated",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"text": {"content": "someone else"}}]},
        }
    ]
    notion.client.blocks.children.list.side_effect = lambda **kwargs: {
        "results": copy.deepcopy(blocks),
        "has_more": False,
    }

    def append(**kwargs):
        added = [dict(b, id="owned-" + str(i)) for i, b in enumerate(kwargs["children"])]
        blocks.extend(added)
        raise TimeoutError("response lost")

    notion.client.blocks.children.append.side_effect = append
    notion.client.blocks.delete.side_effect = lambda block_id: blocks.__setitem__(
        slice(None), [b for b in blocks if b["id"] != block_id]
    )
    notion.client.blocks.retrieve.return_value = {"archived": True}
    storage = NotionCaptureStorage(repository, notion)
    with pytest.raises(TimeoutError):
        storage.save(
            record.id,
            {"action": "append", "target_id": "page", "title": "capture"},
            "full source",
            "summary",
        )
    storage.retract(record.id)
    storage.retract(record.id)
    assert [b["id"] for b in blocks] == ["unrelated"]
    notion.client.pages.update.assert_not_called()


def test_management_api_is_owner_only_and_reports_recovery(setup, monkeypatch):
    repository, settings, _, _, _ = setup
    monkeypatch.setattr(capture_management, "settings", settings)
    client = TestClient(app, base_url="http://localhost")
    assert client.get("/api/v1/captures").status_code == 401
    assert (
        client.get("/api/v1/captures", headers={"X-Voice-Notion-Config": "{}"}).status_code == 401
    )
    record, _ = make_capture(setup, "file")
    assert (
        client.get(
            "/api/v1/captures/" + record.id, headers={"X-API-Key": "test-personal"}
        ).status_code
        == 200
    )


def test_line_clear_preview_buttons_confirm_and_missing_scope(setup, monkeypatch):
    repository, settings, fake, _, service = setup
    record, _ = make_capture(setup, "file")
    monkeypatch.setattr("app.services.line_events.DeletionService", lambda *args: service)
    client = MagicMock()
    processor = LineEventProcessor(repository, settings, MagicMock(), client=client)
    processor.command("/clear " + record.id, "ambiguous")
    assert not any(kind == "trash" for kind, _ in fake.operations)
    processor.command("/clear " + record.id + " all", "preview")
    actions = client.push.call_args.kwargs["actions"]
    assert actions[0]["data"].startswith("confirm:")
    assert not any(kind == "trash" for kind, _ in fake.operations)
    confirm_event = {
        "webhookEventId": "confirm",
        "type": "postback",
        "source": {"type": "user", "userId": "test-owner"},
        "postback": {"data": actions[0]["data"]},
    }
    processor.store.accept(confirm_event)
    processor.process("confirm")
    assert repository.get(record.id).status == "deleted"
    processor.process("confirm")
    assert sum(kind == "trash" for kind, _ in fake.operations) == 1


def test_unsend_webhook_records_intent_before_queue_and_removes_content(setup, monkeypatch):
    import base64
    import hashlib
    import hmac
    import json

    from app.routes import line_webhook

    repository, settings, fake, _, service = setup
    record, _ = make_capture(setup, key="line:message1")
    monkeypatch.setattr(line_webhook, "settings", settings)
    queue = MagicMock()
    monkeypatch.setattr(line_webhook.process_line_event, "delay", queue)
    monkeypatch.setattr("app.services.line_events.DeletionService", lambda *args: service)
    body = json.dumps(
        {
            "events": [
                {
                    "webhookEventId": "unsend",
                    "type": "unsend",
                    "source": {"type": "user", "userId": "test-owner"},
                    "unsend": {"messageId": "message1"},
                }
            ]
        }
    ).encode()
    signature = base64.b64encode(hmac.digest(b"test-channel", body, hashlib.sha256)).decode()
    response = TestClient(app, base_url="http://localhost").post(
        "/api/v1/line/webhook", content=body, headers={"x-line-signature": signature}
    )
    assert response.status_code == 200
    assert repository.get(record.id).status == "delete_pending"
    queue.assert_called_once_with("unsend")
    LineEventProcessor(repository, settings, MagicMock(), client=MagicMock()).process("unsend")
    assert repository.get(record.id).status == "deleted"
    assert all(fake.entries[f.cloud_id]["trashed"] for f in repository.files(record.id))


def test_deleted_source_rejects_foreign_files_and_new_artifacts(setup):
    repository, _, fake, _, service = setup
    record, _ = make_capture(setup, "file")
    file = repository.files(record.id)[0]
    fake.entries[file.cloud_id]["appProperties"]["voice_notion_instance"] = "other-deployment"
    with pytest.raises(ValueError, match="managed capture"):
        service.preview("file", file_id=file.id)
    assert not fake.entries[file.cloud_id]["trashed"]
    with pytest.raises(KeyError):
        service.preview("file", file_id=file.id, owner_id="demo:other")


def test_pending_metadata_survives_queued_completion_and_confirmation_retry(setup, monkeypatch):
    repository, _, fake, storage, service = setup
    record, processor = make_capture(setup)
    service.review(record.id)
    preview = service.preview("original", capture_id=record.id)
    upload = storage.upload
    failed = [True]

    def upload_note(file, **kwargs):
        if file.role == "note" and failed:
            failed.pop()
            raise ConnectionError("offline upload failed")
        return upload(file, **kwargs)

    monkeypatch.setattr(storage, "upload", upload_note)
    assert not service.confirm(preview["token"])["complete"]
    note = next(f for f in repository.files(record.id) if f.role == "note")
    local_path = repository.directory(record.id) / note.name
    assert local_path.exists() and repository.checkpoint(record.id, "note_metadata_pending")
    processor.process(record.id)
    assert local_path.exists()
    assert service.confirm(preview["token"])["complete"]
    assert (
        not local_path.exists()
        and 'original_status: "trashed"' in fake.content[note.cloud_id].decode()
    )


@pytest.mark.parametrize("tampered", [False, True])
def test_inflight_upload_after_intent_is_removed_only_when_bytes_match_preview(setup, tampered):
    import hashlib

    repository, _, fake, storage, service = setup
    record, _ = repository.create(
        owner_id="owner",
        channel="line",
        kind="file",
        destination="drive",
        content=b"approved bytes",
        original_name="file.pdf",
        mime_type="application/pdf",
    )
    folder = storage.ensure_capture_folder(record.id)
    file = repository.files(record.id)[0]
    repository.update_file(file.id, cloud_id="fixed-inflight", state="allocated")
    file = repository.file(file.id)
    preview = service.preview("all", capture_id=record.id)
    execute = service._execute

    def finish_upload(data):
        content = b"tampered bytes" if tampered else b"approved bytes"
        fake.content[file.cloud_id] = content
        fake.entries[file.cloud_id] = {
            "id": file.cloud_id,
            "parents": [folder],
            "appProperties": storage.expected_properties(file),
            "size": str(len(content)),
            "md5Checksum": hashlib.md5(content, usedforsecurity=False).hexdigest(),
            "permissions": [],
            "trashed": False,
        }
        return execute(data)

    service._execute = finish_upload
    result = service.confirm(preview["token"])
    assert result["complete"] is (not tampered)
    assert fake.entries[file.cloud_id]["trashed"] is (not tampered)


def test_metadata_retry_refuses_external_note_edits(setup, monkeypatch):
    repository, _, fake, storage, service = setup
    record, _ = make_capture(setup)
    service.review(record.id)
    preview = service.preview("original", capture_id=record.id)
    upload = storage.upload
    monkeypatch.setattr(storage, "upload", MagicMock(side_effect=ConnectionError("offline")))
    assert not service.confirm(preview["token"])["complete"]
    note = next(f for f in repository.files(record.id) if f.role == "note")
    fake.content[note.cloud_id] = b"user edited this note"
    monkeypatch.setattr(storage, "upload", upload)
    assert not service.confirm(preview["token"])["complete"]
    assert fake.content[note.cloud_id] == b"user edited this note"
    assert (repository.directory(record.id) / note.name).exists()


def test_notion_moved_block_does_not_report_success(setup):
    repository, _, _, _, _ = setup
    record, _ = repository.create(
        owner_id="owner",
        channel="line",
        kind="text",
        destination="notion",
        content=b"full",
        original_name="text",
        mime_type="text/plain",
    )
    repository.save_checkpoint(
        record.id, "notion_page", {"id": "page", "url": "https://notion.so/page"}
    )
    repository.save_checkpoint(
        record.id,
        "notion_parts",
        {"0": {"ids": ["moved-block"], "hashes": ["hash"], "marker": "marker"}},
    )
    notion = MagicMock()
    notion.client.blocks.children.list.return_value = {"results": [], "has_more": False}
    notion.client.blocks.retrieve.return_value = {
        "id": "moved-block",
        "archived": False,
        "parent": {"page_id": "elsewhere"},
    }
    with pytest.raises(RuntimeError, match="moved"):
        NotionCaptureStorage(repository, notion).retract(record.id)
    notion.client.blocks.delete.assert_not_called()
    notion.client.pages.update.assert_not_called()


@pytest.mark.parametrize("unrelated", [False, True])
def test_notion_created_page_title_is_removed_without_deleting_other_notes(setup, unrelated):
    repository, _, _, _, _ = setup
    record, _ = repository.create(
        owner_id="owner",
        channel="line",
        kind="text",
        destination="notion",
        content=b"full",
        original_name="text",
        mime_type="text/plain",
    )
    title = "private topic · " + record.id
    repository.save_checkpoint(
        record.id,
        "notion_page",
        {
            "id": "own-page",
            "url": "https://notion.so/own-page",
            "created_for_capture": True,
            "original_title": title,
            "parent_id": "root",
        },
    )
    notion = MagicMock()
    notion.client.pages.retrieve.return_value = {
        "id": "own-page",
        "properties": {"title": {"title": [{"plain_text": title}]}},
    }
    notion.client.blocks.children.list.return_value = {
        "results": [{"id": "unrelated"}] if unrelated else [],
        "has_more": False,
    }
    NotionCaptureStorage(repository, notion).retract(record.id)
    kwargs = notion.client.pages.update.call_args.kwargs
    if unrelated:
        assert (
            kwargs["properties"]["title"]["title"][0]["text"]["content"] == record.id
            and "archived" not in kwargs
        )
    else:
        assert kwargs["archived"] is True


@pytest.mark.parametrize("when", ["before_preview", "before_confirm", "after_intent"])
def test_original_shortcut_requires_retained_fulltext_to_stay_valid(setup, when):
    repository, _, fake, _, service = setup
    record, _ = make_capture(setup)
    service.review(record.id)
    files = {f.role: f for f in repository.files(record.id)}
    if when == "before_preview":
        fake.content[files["transcript"].cloud_id] = b"changed after human review"
        with pytest.raises(DeletionConflict):
            service.preview("original", capture_id=record.id)
    else:
        preview = service.preview("original", capture_id=record.id)
        if when == "before_confirm":
            fake.entries[files["transcript"].cloud_id]["trashed"] = True
        else:
            execute = service._execute

            def remove_transcript(data):
                fake.entries[files["transcript"].cloud_id]["trashed"] = True
                return execute(data)

            service._execute = remove_transcript
        with pytest.raises(DeletionConflict):
            service.confirm(preview["token"])
    assert not fake.entries[files["original"].cloud_id]["trashed"]


def test_reviewed_batch_retry_ignores_metadata_updated_by_its_completed_items(setup):
    repository, _, fake, _, service = setup
    first, _ = make_capture(setup)
    second, _ = make_capture(setup)
    service.review(first.id)
    service.review(second.id)
    target = next(f for f in repository.files(second.id) if f.role == "original").cloud_id
    update = fake.update
    failed = [True]

    def fail_once(fileId, **kwargs):
        if fileId == target and kwargs.get("body", {}).get("trashed") and failed:
            failed.pop()
            return DriveCall(lambda: (_ for _ in ()).throw(ConnectionError("offline")))
        return update(fileId, **kwargs)

    fake.update = fail_once
    preview = service.preview("reviewed")
    assert not service.confirm(preview["token"])["complete"]
    assert service.confirm(preview["token"])["complete"]
    assert sum(kind == "trash" for kind, _ in fake.operations) == 2


def create_local_cleanup_files(repository, count):
    file_ids = []
    for _ in range(count):
        record, _ = repository.create(
            owner_id="owner",
            channel="http",
            kind="file",
            destination="drive",
            content=b"local test source",
            original_name="file.pdf",
            mime_type="application/pdf",
        )
        file_ids.append(repository.files(record.id)[0].id)
    return file_ids


@pytest.mark.parametrize("status", ["pending", "running", "done"])
def test_line_rejects_oversized_api_tokens_before_result_or_cleanup(setup, monkeypatch, status):
    repository, settings, fake, _, service = setup
    file_ids = create_local_cleanup_files(repository, 46)
    preview = service.preview("files", file_ids=file_ids)
    if status == "done":
        assert service.confirm(preview["token"])["complete"]
    elif status == "running":
        with repository.connection() as connection:
            connection.execute("UPDATE deletion_previews SET status='running'")
    before = [repository.file(file_id).model_dump() for file_id in file_ids]
    before_preview = service._load(preview["token"], "owner")
    execute = MagicMock(side_effect=AssertionError("Oversized LINE confirmation executed cleanup"))
    monkeypatch.setattr(service, "_execute", execute)
    monkeypatch.setattr("app.services.line_events.DeletionService", lambda *args: service)
    client = MagicMock()
    LineEventProcessor(repository, settings, MagicMock(), client=client).command(
        "/confirm " + preview["token"], "large-confirm"
    )
    assert "LINE未套用本次確認" in client.push.call_args.args[0]
    assert "請透過管理 API 完成操作或查看完整結果" in client.push.call_args.args[0]
    assert "未執行清理" not in client.push.call_args.args[0]
    assert service._load(preview["token"], "owner") == before_preview
    assert [repository.file(file_id).model_dump() for file_id in file_ids] == before
    execute.assert_not_called()
    assert fake.operations == []
    with pytest.raises(KeyError):
        service.confirm(preview["token"], owner_id="foreign", max_result_files=45)


@pytest.mark.parametrize("count", [45, 46])
def test_api_default_confirmation_is_not_restricted_to_line_result_limit(setup, monkeypatch, count):
    repository, settings, _, _, service = setup
    file_ids = create_local_cleanup_files(repository, count)
    monkeypatch.setattr(capture_management, "settings", settings)
    client = TestClient(app, base_url="http://localhost")
    response = client.post(
        "/api/v1/deletions/preview",
        json={"scope": "files", "file_ids": file_ids},
        headers={"X-API-Key": "test-personal"},
    )
    assert response.status_code == 200
    token = response.json()["token"]
    if count == 45:
        result = service.confirm(token, max_result_files=45)
    else:
        response = client.post(
            "/api/v1/deletions/confirm",
            json={"token": token},
            headers={"X-API-Key": "test-personal"},
        )
        assert response.status_code == 200
        result = response.json()
    assert result["complete"] and len(result["files"]) == count
    assert all(repository.file(file_id).state == "trashed" for file_id in file_ids)


def test_result_count_guard_preserves_owner_expiry_and_cancel_checks(setup):
    repository, _, _, _, service = setup
    file_ids = create_local_cleanup_files(repository, 1)
    preview = service.preview("files", file_ids=file_ids)
    with pytest.raises(KeyError):
        service.confirm(preview["token"], owner_id="foreign", max_result_files=45)
    service.cancel(preview["token"])
    with pytest.raises(DeletionConflict, match="取消"):
        service.confirm(preview["token"], max_result_files=45)
    preview = service.preview("files", file_ids=file_ids)
    with repository.connection() as connection:
        connection.execute("UPDATE deletion_previews SET expires=0")
    with pytest.raises(DeletionConflict, match="過期"):
        service.confirm(preview["token"], max_result_files=45)
    assert repository.file(file_ids[0]).state == "local"
