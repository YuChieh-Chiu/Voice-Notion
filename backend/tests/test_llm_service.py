import pytest
import os
from unittest.mock import MagicMock, patch, mock_open
from app.services.llm_service import LLMService

@pytest.fixture
def llm_service():
    with patch("app.services.llm_service.genai.Client"):
        service = LLMService()
        return service

def test_route_success(llm_service):
    """測試路由判斷功能"""
    mock_response = MagicMock()
    mock_response.text = '{"action": "create", "title": "測試會議", "template_type": "meeting", "target_page_id": "page_123"}'
    llm_service.client.models.generate_content.return_value = mock_response

    transcript = "今天下午兩點要開週會，討論下週的開發進度。"
    available_pages = [{"title": "工作筆記", "id": "page_123"}]
    
    result = llm_service.route(transcript, available_pages)
    
    assert result["action"] == "create"
    assert result["template_type"] == "meeting"
    assert result["target_page_id"] == "page_123"
    assert llm_service.client.models.generate_content.called

def test_summarize_success(llm_service):
    """測試摘要生成功能"""
    mock_response = MagicMock()
    mock_response.text = "## 會議摘要\n- 討論開發進度"
    llm_service.client.models.generate_content.return_value = mock_response

    transcript = "開會內容很長..."
    template_type = "meeting"
    
    # 模擬模板讀取
    with patch("builtins.open", mock_open(read_data="模板內容: {transcript}")):
        summary = llm_service.summarize(transcript, template_type)
        
    assert "會議摘要" in summary
    assert llm_service.client.models.generate_content.called

def test_summarize_template_not_found(llm_service):
    """測試模板不存在時的回退機制"""
    mock_response = MagicMock()
    mock_response.text = "通用摘要內容"
    llm_service.client.models.generate_content.return_value = mock_response

    # 模擬模板路徑不存在
    with patch("app.services.llm_service.Path.exists", return_value=False):
         # 再次模擬 open 以防止報錯
         with patch("builtins.open", mock_open(read_data="Fallback template: {transcript}")):
            summary = llm_service.summarize("內容", "invalid_type")
    
    assert summary == "通用摘要內容"
