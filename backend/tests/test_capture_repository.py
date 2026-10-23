import hashlib

import pytest
from pydantic import ValidationError

from app.config import CaptureCategory, Settings
from app.services.capture_repository import CaptureRepository


def create(repository, **overrides):
    arguments = dict(
        owner_id="owner",
        channel="http",
        kind="text",
        destination="drive",
        content="完整原文".encode(),
        original_name="message.txt",
        mime_type="text/plain",
    )
    return repository.create(**(arguments | overrides))


def test_source_bytes_survive_reopen_and_redelivery(tmp_path):
    repository = CaptureRepository(tmp_path)
    capture, created = create(repository, source_key="line:message-1")
    repeated, duplicate = create(repository, source_key="line:message-1", content=b"different")
    assert created and not duplicate and repeated.id == capture.id
    reopened = CaptureRepository(tmp_path)
    original = reopened.files(capture.id)[0]
    assert reopened.read_file(original) == "完整原文".encode()
    assert original.sha256 == hashlib.sha256("完整原文".encode()).hexdigest()
    assert reopened.database.stat().st_mode & 0o777 == 0o600


def test_source_identity_is_scoped_to_owner(tmp_path):
    repository = CaptureRepository(tmp_path)
    capture, _ = create(repository, source_key="request-1")
    other, _ = create(repository, owner_id="demo:other", source_key="request-1")
    assert capture.id != other.id
    with pytest.raises(KeyError):
        repository.get(capture.id, owner_id="demo:other")
    assert len(repository.list_captures("owner")) == 1


def test_artifacts_and_deleted_source_cannot_be_recreated(tmp_path):
    repository = CaptureRepository(tmp_path)
    capture, _ = create(repository)
    note = repository.save_artifact(capture.id, "note", b"summary")
    repository.update_file(note.id, state="trashed")
    with pytest.raises(ValueError, match="cannot be recreated"):
        repository.save_artifact(capture.id, "note", b"retry")
    repository.update_capture(capture.id, status="delete_pending")
    with pytest.raises(ValueError, match="removed"):
        repository.save_artifact(capture.id, "transcript", b"retry")


def test_paths_are_server_generated_and_original_is_immutable(tmp_path):
    repository = CaptureRepository(tmp_path)
    capture, _ = create(repository, original_name="../../outside.txt")
    assert capture.original_name == "outside.txt"
    assert repository.files(capture.id)[0].name == "original.txt"
    with pytest.raises(ValueError):
        repository.directory("../../outside")
    with pytest.raises(ValueError, match="overwritten"):
        repository.save_artifact(capture.id, "original", b"replacement")
    assert not (tmp_path / "outside.txt").exists()


def test_secrets_are_excluded_from_public_record_and_updates_are_allowlisted(tmp_path):
    repository = CaptureRepository(tmp_path)
    capture, _ = create(repository, encrypted_context="test-ciphertext")
    assert "encrypted_context" not in repository.get(capture.id).model_dump()
    assert repository.get(capture.id).encrypted_context == "test-ciphertext"
    with pytest.raises(ValueError):
        repository.update_capture(capture.id, owner_id="attacker")
    with pytest.raises(ValueError):
        repository.update_file(repository.files(capture.id)[0].id, name="../../outside")


def test_inactive_destinations_need_no_credentials():
    settings = Settings(
        _env_file=None, GEMINI_API_KEY="", NOTION_TOKEN="", CAPTURE_DESTINATION="drive"
    )
    assert settings.NOTION_TOKEN == ""
    with pytest.raises(ValidationError):
        CaptureCategory(id="../../outside", description="invalid")
    with pytest.raises(ValidationError, match="unique"):
        Settings(_env_file=None, CAPTURE_CATEGORIES=[{"id": "finance", "description": "a"}] * 2)
