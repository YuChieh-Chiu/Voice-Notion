from unittest.mock import patch

import pytest

from app.services.notion_service import NotionService, _token_caches


@pytest.fixture
def notion_service():
    _token_caches.clear()
    with patch("app.services.notion_service.Client"):
        return NotionService()


def test_sync_page_tree_with_paginated_children(notion_service):
    notion_service.client.search.return_value = {
        "results": [
            {
                "id": "root",
                "parent": {"type": "workspace"},
                "properties": {"title": {"type": "title", "title": [{"plain_text": "工作"}]}},
            }
        ],
        "has_more": False,
    }
    notion_service.client.blocks.children.list.side_effect = [
        {"results": [], "has_more": True, "next_cursor": "next"},
        {
            "results": [{"id": "child", "type": "child_page", "child_page": {"title": "筆記"}}],
            "has_more": False,
        },
    ]
    tree = notion_service.sync_page_tree()
    assert tree["roots"] == [{"id": "root", "title": "工作"}]
    assert tree["subpages"] == [{"id": "child", "parent_id": "root", "title": "筆記"}]
    assert notion_service.client.blocks.children.list.call_count == 2


def test_create_subpage_keeps_existing_interface(notion_service):
    notion_service.client.pages.create.return_value = {"url": "https://notion.so/new", "id": "new"}
    result = notion_service.create_subpage("root", "標題", "摘要")
    assert result == {"url": "https://notion.so/new", "id": "new"}
    assert notion_service.client.pages.create.call_args.kwargs["parent"] == {"page_id": "root"}


def test_append_to_page_keeps_existing_interface(notion_service):
    notion_service.client.pages.retrieve.return_value = {"url": "https://notion.so/existing"}
    assert notion_service.append_to_page("page", "標題", "摘要") == "https://notion.so/existing"
    assert notion_service.client.blocks.children.append.called


def test_failed_tree_lookup_does_not_log_token_or_response(notion_service, caplog):
    notion_service.client.search.side_effect = RuntimeError("test-sensitive-provider-payload")
    with pytest.raises(RuntimeError, match="lookup failed"):
        notion_service.sync_page_tree()
    assert "test-sensitive-provider-payload" not in caplog.text
    assert notion_service.auth_token not in caplog.text
