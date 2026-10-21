from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.services.capture_processor import CaptureProcessor
from app.services.capture_repository import CaptureRepository
from app.services.notion_capture_storage import NotionCaptureStorage, NotionWriteUncertain


@pytest.fixture
def pipeline(tmp_path):
    repository = CaptureRepository(tmp_path)
    settings = Settings(_env_file=None, CAPTURE_DATA_DIR=tmp_path)
    notion, llm, notifier, stt = (MagicMock() for _ in range(4))
    routing = {"action": "append", "target_id": "page", "title": "筆記", "template_type": "general"}
    llm.route.return_value = routing
    llm.summarize.return_value = "摘要內容"
    notion.client.pages.retrieve.return_value = {"id": "page", "url": "https://notion.so/page"}
    blocks = []
    notion.client.blocks.children.list.side_effect = lambda **kwargs: {
        "results": list(blocks),
        "has_more": False,
    }
    notion.client.blocks.children.append.side_effect = lambda **kwargs: blocks.extend(
        kwargs["children"]
    )
    processor = CaptureProcessor(
        repository,
        settings,
        stt_factory=lambda: stt,
        llm_factory=lambda **kwargs: llm,
        notion_factory=lambda **kwargs: notion,
        notifier_factory=lambda **kwargs: notifier,
    )
    return repository, processor, notion, llm, notifier, stt, blocks


def capture(repository, kind="text"):
    return repository.create(
        owner_id="owner",
        channel="http",
        kind=kind,
        destination="notion",
        content="完整來源與摘要不同".encode(),
        original_name="source.txt" if kind == "text" else "source.m4a",
        mime_type="text/plain" if kind == "text" else "audio/mp4",
    )[0]


def test_full_source_is_stored_and_notification_retry_does_not_repeat_models(pipeline):
    repository, processor, notion, llm, notifier, _, blocks = pipeline
    record = capture(repository)
    notifier.push_message.side_effect = [RuntimeError("notification unavailable"), None]
    with pytest.raises(RuntimeError):
        processor.process(record.id)
    assert repository.get(record.id).status == "complete"
    assert not list(repository.directory(record.id).iterdir())
    assert processor.process(record.id)["notion_url"] == "https://notion.so/page"
    assert llm.summarize.call_count == 1
    assert notion.client.blocks.children.append.call_count == 1
    content = str(blocks)
    assert "完整來源與摘要不同" in content and "摘要內容" in content
    assert "audio" not in {block["type"] for block in blocks}
    assert not list(repository.directory(record.id).iterdir())


def test_transcript_survives_summary_failure_without_repeating_stt(pipeline):
    repository, processor, _, llm, _, stt, _ = pipeline
    record = capture(repository, "audio")
    stt.transcribe.return_value = "完整未潤飾逐字稿"
    llm.summarize.side_effect = [RuntimeError("provider failure"), "重試摘要"]
    with pytest.raises(RuntimeError):
        processor.process(record.id)
    assert repository.get(record.id).status == "failed"
    processor.process(record.id)
    assert stt.transcribe.call_count == 1
    assert llm.route.call_count == 1


def test_removed_capture_does_no_external_work(pipeline):
    repository, processor, notion, llm, _, _, _ = pipeline
    record = capture(repository)
    repository.update_capture(record.id, status="delete_pending")
    assert processor.process(record.id)["status"] == "removed"
    assert not notion.client.pages.retrieve.called and not llm.summarize.called


def test_notion_never_accepts_original_attachments(pipeline):
    repository, processor, _, llm, _, _, _ = pipeline
    record = capture(repository, "file")
    with pytest.raises(ValueError, match="attachments"):
        processor.process(record.id)
    assert not llm.summarize.called


def test_large_notion_source_is_chunked_without_losing_text(pipeline):
    repository, _, notion, _, _, _, blocks = pipeline
    record = capture(repository)
    source = "文字" * 45_000
    storage = NotionCaptureStorage(repository, notion)
    routing = {"action": "append", "target_id": "page", "title": "長文"}
    storage.save(record.id, routing, source, "短摘要")
    fragments = [
        part["text"]["content"] for block in blocks for part in block[block["type"]]["rich_text"]
    ]
    assert all(len(fragment) <= 1800 for fragment in fragments)
    assert sum(fragment.count("文字") for fragment in fragments) == 45_000
    assert all(
        len(call.kwargs["children"]) <= 41
        for call in notion.client.blocks.children.append.call_args_list
    )
    before = notion.client.blocks.children.append.call_count
    storage.save(record.id, routing, source, "短摘要")
    assert notion.client.blocks.children.append.call_count == before


def test_uncertain_notion_write_is_not_blindly_repeated(pipeline):
    repository, _, notion, _, _, _, _ = pipeline
    record = capture(repository)
    storage = NotionCaptureStorage(repository, notion)
    routing = {"action": "append", "target_id": "page", "title": "筆記"}
    notion.client.blocks.children.append.side_effect = TimeoutError("unknown write result")
    with pytest.raises(TimeoutError):
        storage.save(record.id, routing, "來源", "摘要")
    with pytest.raises(NotionWriteUncertain):
        storage.save(record.id, routing, "來源", "摘要")
    assert notion.client.blocks.children.append.call_count == 1
