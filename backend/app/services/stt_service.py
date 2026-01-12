"""
STT Service - Faster-Whisper
使用 faster-whisper 於 CPU 執行語音轉文字
"""
from app.core.logger import get_logger

logger = get_logger(__name__)


class STTService:
    """Speech-to-Text Service"""
    
    def __init__(self):
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            logger.error("faster-whisper is not installed. This service should only run in a worker container.")
            raise
            
        # NOTE: 如有需要且有足夠的資源，可以換更強的 model
        # 使用 small model, CPU, int8 量化
        # Faster Whisper 的 model 選擇可以參考 https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/utils.py
        self.model = WhisperModel("small", device="cpu", compute_type="int8")
        logger.info("Faster-Whisper model loaded (small/CPU)")
    
    def transcribe(self, audio_path: str) -> str:
        """
        轉錄音訊檔案為文字
        
        Args:
            audio_path: 音訊檔案路徑
            
        Returns:
            轉錄的文字內容
        """
        try:
            segments, info = self.model.transcribe(
                audio_path,
                language="zh",  # 中文
                vad_filter=True  # 語音活動檢測
            )
            
            # 組合所有片段
            transcript = " ".join([segment.text for segment in segments])
            
            logger.info(f"Transcription completed: {len(transcript)} chars")
            return transcript.strip()
            
        except Exception as e:
            logger.error(f"STT failed: {e}", exc_info=True)
            raise
