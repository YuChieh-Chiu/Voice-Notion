import pytest
from unittest.mock import MagicMock, patch
from app.services.stt_service import STTService

@pytest.fixture
def stt_service():
    # 模擬 WhisperModel 載入，避免在測試環境真的跑模型（太重）
    with patch("app.services.stt_service.WhisperModel"):
        service = STTService()
        return service

def test_transcribe_success(stt_service):
    """測試語音轉文字功能"""
    # 模擬 segment 物件
    mock_segment = MagicMock()
    mock_segment.text = "這是一段測試文字"
    
    # 模擬 model.transcribe 的回傳值 (segments, info)
    stt_service.model.transcribe.return_value = ([mock_segment], MagicMock())
    
    result = stt_service.transcribe("fake_audio.m4a")
    
    assert result == "這是一段測試文字"
    assert stt_service.model.transcribe.called

def test_transcribe_failure(stt_service):
    """測試轉錄失敗的情況"""
    stt_service.model.transcribe.side_effect = Exception("STT Error")
    
    with pytest.raises(Exception) as excinfo:
        stt_service.transcribe("fake_audio.m4a")
    
    assert "STT Error" in str(excinfo.value)
