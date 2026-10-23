from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.schemas.context import AuthType, UserContext
from app.services import line_messages as messages
from app.services.notification_service import NotificationService

CID = "C0123456789abcdef"
FID = "F0123456789abcdef"
LINK = "https://drive.google.com/file/d/test/view"


def test_notification_layout_labels_title_without_claiming_it_is_a_summary():
    text = messages.completion("跑步計畫\n狀態：已校對", LINK, capture_id=CID)
    assert text.startswith("Note-Goblin｜保存完成\n紀錄ID：" + CID + "\n狀態：")
    assert "標題：跑步計畫 狀態：已校對" in text
    assert "\n狀態：已校對" not in text and "摘要：跑步計畫" not in text
    assert (
        text.index("內容：")
        < text.index("連結：")
        < text.index("下一步：")
        < text.index("必要提醒：")
    )
    assert "尚未人工校對" in text and "/files " + CID in text
    assert "同步" in text


@pytest.mark.parametrize("capture_id", [None, "", "task-uuid", "F0123456789abcdef", CID + "\n假造"])
def test_absent_or_non_capture_ids_do_not_become_record_identifiers(capture_id):
    text = messages.completion("標題", "", capture_id=capture_id)
    assert "紀錄ID：" not in text and "\n連結：" not in text
    assert "/files" not in text


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://example.org/\n狀態",
        "https://test-user:test-secret@example.org/",
        "https://example.org/\u202e",
    ],
)
def test_links_reject_unsafe_schemes_credentials_or_control_characters(url):
    assert messages.private_link(url) == ""


def test_long_external_title_is_one_bounded_line_and_not_empty_identifier():
    text = messages.completion("字" * 10000, LINK)
    title = next(line for line in text.splitlines() if line.startswith("標題："))
    assert len(title) <= 183 and title.endswith("…")
    assert len(text) < 1000 and "紀錄ID：" not in text


def test_notion_and_byok_do_not_offer_owner_management_or_drive_sync(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr("app.services.notification_service.LineClient", lambda settings: client)
    service = NotificationService(
        UserContext(
            type=AuthType.DEMO,
            gemini_key="test-key",
            notion_token="test-notion",
            line_token="test-line",
            line_user_id="test-user",
        )
    )
    service.push_message("標題", "https://notion.so/test", capture_id=CID)
    text = client.push.call_args.args[0]
    assert "目的地：Notion" in text and "Drive" not in text and "同步" not in text
    assert "/files" not in text and "/review" not in text
    assert client.push.call_args.kwargs["actions"] == []
    assert client.push.call_args.kwargs["key"] == "complete:" + CID
    personal = messages.completion(
        "標題", "https://notion.so/test", capture_id=CID, destination="notion"
    )
    assert "Drive" not in personal and "/files " + CID in personal


def test_attachments_and_partial_never_claim_fulltext_review_is_available():
    attachment = messages.completion("image.png", LINK, capture_id=CID, original_only=True)
    assert "僅保存原檔，未解析" in attachment and "原文與摘要" not in attachment
    partial = messages.partial(CID)
    assert "原檔已保存" in partial and "文字處理尚未完成" in partial
    assert "\n狀態：保存完成" not in partial and "/review" not in partial


def test_files_preserves_ids_roles_sizes_statuses_and_omits_removed_links():
    record = SimpleNamespace(
        id=CID,
        category="tech\n假造狀態",
        destination="drive",
        kind="audio",
        status="complete",
        reviewed_at=None,
    )
    files = [
        SimpleNamespace(id=FID, role="original", size_bytes=123, state="trashed", cloud_url=LINK),
        SimpleNamespace(
            id="Ffedcba9876543210",
            role="transcript",
            size_bytes=456,
            state="uploaded",
            cloud_url="https://drive.google.com/file/d/alive/view",
        ),
    ]
    text = messages.capture_details(record, files)
    for item in (
        FID,
        "original",
        "transcript",
        "123 bytes",
        "456 bytes",
        "已移至垃圾桶",
        "雲端已保存",
    ):
        assert item in text
    assert LINK not in text and "https://drive.google.com/file/d/alive/view" in text
    assert "分類：tech 假造狀態" in text
    record.kind = "image"
    text = messages.capture_details(record, files[:1])
    assert "僅保存原檔" in text and "校對" not in text


def preview_data():
    return {
        "token": "x" * 32,
        "expires_at": 0,
        "count": 1,
        "total_bytes": 123,
        "files": [{"id": FID, "capture_id": CID, "role": "original", "bytes": 123}],
        "warning": "僅移至垃圾桶，仍占容量。可能移除唯一內容，原檔移除後不能重轉錄。",
    }


def test_preview_keeps_exact_list_scope_utc_expiry_and_action_tokens():
    preview = preview_data()
    text = messages.cleanup_preview(
        preview,
        scope="指定原始音檔（original）",
        filters=["主題：fitness", "UTC 收件日期早於：2026-10-23"],
    )
    for item in (
        CID,
        FID,
        "original",
        "123 bytes",
        "合計：123 bytes",
        "original）",
        "1970-01-01 00:00:00 UTC",
        "fitness",
        "2026-10-23",
        "只處理這份清單",
        "垃圾桶",
        "仍占容量",
        "唯一內容",
        "不能重轉錄",
        "/confirm " + preview["token"],
        "/cancel " + preview["token"],
    ):
        assert item in text


def test_long_preview_is_not_silently_truncated_before_caller_length_guard():
    preview = preview_data()
    preview["warning"] += "說" * 4400
    text = messages.cleanup_preview(preview, scope="整筆")
    assert len(text) > 4400
    assert preview["warning"] in text and "/confirm " + preview["token"] in text


def test_partial_cleanup_lists_outcomes_metadata_errors_and_safe_recovery():
    outcome = {
        "complete": False,
        "files": [
            {"file_id": FID, "status": "missing"},
            {"file_id": "Ffedcba9876543210", "status": "failed", "error_code": "PermissionError"},
        ],
        "metadata_errors": [{"capture_id": CID, "error_code": "DeletionConflict"}],
        "message": "垃圾桶仍占容量；永久刪除請至 Drive 操作。",
    }
    text = messages.cleanup_result(outcome, token="test-token", capture_ids=[CID])
    for item in (
        FID,
        "檔案已不存在",
        "失敗",
        "來源狀態尚未更新",
        "人工調和",
        "/confirm test-token",
        "垃圾桶仍占容量",
    ):
        assert item in text
    assert "\n狀態：清理完成" not in text


@pytest.mark.parametrize("status", ["running", "done"])
def test_cancel_does_not_claim_executed_work_is_cancelled_or_restored(status):
    text = messages.cleanup_cancel(status)
    assert "清單已取消" not in text and "不會復原" in text
    assert "取消未套用" in text


def test_retraction_only_lists_known_capture_and_preserves_scope_exclusions():
    for capture_id in (None, CID):
        text = messages.retraction(capture_id)
        assert ("紀錄ID：" in text) == bool(capture_id)
        assert "另存副本" in text and "Atlas" in text and "供應商備份" in text


def test_completion_retry_uses_existing_human_review_state():
    text = messages.completion("標題", LINK, capture_id=CID, reviewed=True)
    assert "已人工校對" in text and "尚未人工校對" not in text


def test_every_acceptable_preview_has_room_for_all_outcomes_and_metadata_errors():
    accepted = 0
    for count in range(1, 101):
        preview = preview_data()
        preview["files"] = [
            {
                "id": "F" + format(i, "016x"),
                "capture_id": "C" + format(i, "016x"),
                "role": "note",
                "bytes": 1,
            }
            for i in range(count)
        ]
        preview["count"] = preview["total_bytes"] = count
        if len(messages.cleanup_preview(preview, scope="files")) > 4400:
            break
        capture_ids = [f["capture_id"] for f in preview["files"]]
        outcome = {
            "complete": False,
            "files": [{"file_id": f["id"], "status": "missing"} for f in preview["files"]],
            "metadata_errors": [
                {"capture_id": cid, "error_code": "NetworkError" * 100} for cid in capture_ids
            ],
            "message": "垃圾桶仍占容量；永久刪除請至 Drive 操作。",
        }
        result = messages.cleanup_result(outcome, token=preview["token"], capture_ids=capture_ids)
        assert len(result) < 4500
        assert all(f["id"] in result for f in preview["files"])
        assert all(cid + "｜來源狀態尚未更新" in result for cid in capture_ids)
        assert "/confirm " + preview["token"] in result and "人工調和" in result
        assert "垃圾桶仍占容量" in result
        accepted += 1
    assert accepted == 45
