from unittest.mock import MagicMock

import pytest
from drive_fakes import FakeDrive

from app.config import Settings
from app.services.capture_processor import CaptureProcessor
from app.services.capture_repository import CaptureRepository
from app.services.drive_storage import DriveStorage


@pytest.fixture
def drive_setup(tmp_path):
    repository = CaptureRepository(tmp_path)
    settings = Settings(_env_file=None, DRIVE_FOLDER_ID="root", CAPTURE_DESTINATION="drive")
    fake = FakeDrive()
    return repository, settings, fake, DriveStorage(repository, settings, service=fake)


def create(repository, kind="text", content=b"complete source"):
    return repository.create(
        owner_id="owner",
        channel="http",
        kind=kind,
        destination="drive",
        content=content,
        original_name="file.pdf" if kind == "file" else "source.txt",
        mime_type="application/octet-stream",
    )[0]


def test_upload_reuses_cloud_id_and_preserves_received_bytes(drive_setup):
    repository, settings, fake, storage = drive_setup
    record = create(repository)
    original = storage.upload(repository.files(record.id)[0])
    assert fake.content[original.cloud_id] == b"complete source"
    before = list(fake.operations)
    storage.upload(repository.file(original.id))
    assert fake.operations == before
    reopened = DriveStorage(CaptureRepository(repository.root.parent), settings, service=fake)
    assert reopened.instance_id == storage.instance_id


def test_interrupted_upload_resumes_same_file_and_offset(drive_setup):
    repository, _, fake, storage = drive_setup
    record = create(repository)
    original = repository.files(record.id)[0]
    fake.interrupt_upload = True
    with pytest.raises(ConnectionError):
        storage.upload(original)
    assigned = repository.file(original.id)
    checkpoint = repository.checkpoint(record.id, "drive_upload:" + original.id)
    assert checkpoint["offset"] > 0
    result = storage.upload(assigned)
    assert result.cloud_id == assigned.cloud_id
    assert fake.upload_offsets[-1] == checkpoint["offset"]
    assert fake.content[result.cloud_id] == b"complete source"


def test_public_folder_and_foreign_content_are_rejected(drive_setup):
    repository, _, fake, storage = drive_setup
    record = create(repository)
    fake.entries["root"]["permissions"] = [{"type": "anyone"}]
    with pytest.raises(ValueError, match="privately"):
        storage.upload(repository.files(record.id)[0])
    fake.entries["root"]["permissions"] = []
    original = storage.upload(repository.files(record.id)[0])
    fake.entries[original.cloud_id]["appProperties"]["voice_notion_instance"] = "another-instance"
    with pytest.raises(ValueError, match="managed capture"):
        storage.upload(repository.file(original.id))


def test_moved_capture_folder_is_not_silently_moved_back(drive_setup):
    repository, _, fake, storage = drive_setup
    record = create(repository)
    original = storage.upload(repository.files(record.id)[0])
    fake.entries[repository.get(record.id).folder_id]["parents"] = ["outside-root"]
    with pytest.raises(ValueError, match="configured Drive roots"):
        storage.upload(repository.file(original.id))


@pytest.mark.parametrize("kind,expected", [("text", 2), ("audio", 3), ("image", 1), ("file", 1)])
def test_pipeline_preserves_only_planned_files_without_notion(drive_setup, kind, expected):
    repository, settings, fake, storage = drive_setup
    record = create(repository, kind)
    stt, llm, notifier = MagicMock(), MagicMock(), MagicMock()
    stt.transcribe.return_value = "完整未潤飾逐字稿"
    llm.summarize.return_value = "摘要"
    forbidden_notion = MagicMock(side_effect=AssertionError("Notion must be independent"))
    processor = CaptureProcessor(
        repository,
        settings,
        drive_factory=lambda *args: storage,
        stt_factory=lambda: stt,
        llm_factory=lambda **kwargs: llm,
        notifier_factory=lambda **kwargs: notifier,
        notion_factory=forbidden_notion,
    )
    result = processor.process(record.id)
    uploaded = [file for file in repository.files(record.id) if file.state == "uploaded"]
    assert len(uploaded) == expected and result["url"].startswith("https://drive.google.com/")
    assert not list(repository.directory(record.id).iterdir())
    if kind in ("image", "file"):
        assert not llm.summarize.called and not stt.transcribe.called
    assert (
        fake.content[next(file for file in uploaded if file.role == "original").cloud_id]
        == b"complete source"
    )


def test_summary_failure_keeps_original_cloud_file_and_resumes(drive_setup):
    repository, settings, fake, storage = drive_setup
    record = create(repository)
    llm = MagicMock()
    llm.summarize.side_effect = [RuntimeError("test failure"), "summary"]
    processor = CaptureProcessor(
        repository,
        settings,
        drive_factory=lambda *args: storage,
        llm_factory=lambda **kwargs: llm,
        notifier_factory=lambda **kwargs: MagicMock(),
    )
    with pytest.raises(RuntimeError):
        processor.process(record.id)
    original = repository.files(record.id)[0]
    assert original.state == "uploaded" and fake.content[original.cloud_id] == b"complete source"
    processor.process(record.id)
    assert sum(op == ("upload", original.cloud_id) for op in fake.operations) == 1
