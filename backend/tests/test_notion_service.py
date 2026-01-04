import pytest
from unittest.mock import MagicMock, patch
from app.services.notion_service import NotionService

@pytest.fixture
def notion_service():
    with patch("app.services.notion_service.Client"):
        service = NotionService()
        return service

def test_sync_available_pages(notion_service):
    """測試同步可用頁面"""
    notion_service.client.search.return_value = {
        "results": [
            {"id": "page_1", "properties": {"title": {"title": [{"plain_text": "工作"}]}}},
            {"id": "page_2", "properties": {"Name": {"title": [{"plain_text": "筆記"}]}}}
        ],
        "has_more": False
    }
    
    pages = notion_service.sync_available_pages()
    
    assert len(pages) == 2
    assert pages[0]["title"] == "工作"
    assert pages[1]["title"] == "筆記"

def test_create_page(notion_service):
    """測試建立頁面"""
    notion_service.client.pages.create.return_value = {"url": "https://notion.so/new_page"}
    
    data = {"title": "標題", "summary": "內容"}
    url = notion_service.create_page("parent_id", data)
    
    assert url == "https://notion.so/new_page"
    assert notion_service.client.pages.create.called

def test_append_to_page(notion_service):
    """測試追加內容"""
    notion_service.client.blocks.children.append.return_value = {}
    notion_service.client.pages.retrieve.return_value = {"url": "https://notion.so/existing_page"}
    
    data = {"title": "標題", "summary": "內容"}
    url = notion_service.append_to_page("page_id", data)
    
    assert url == "https://notion.so/existing_page"
    assert notion_service.client.blocks.children.append.called
    assert notion_service.client.pages.retrieve.called
