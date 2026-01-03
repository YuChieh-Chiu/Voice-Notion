"""
Celery Background Tasks
處理語音筆記的完整 Pipeline
"""
import os
from app.core.celery_app import celery_app
from app.services.stt_service import STTService
from app.services.llm_service import LLMService
from app.services.notion_service import NotionService
from app.services.notification_service import NotificationService
from app.core.logger import get_logger

logger = get_logger(__name__)


@celery_app.task(bind=True, max_retries=3)
def process_voice_note(self, file_path: str):
    """
    處理語音筆記 Pipeline
    
    1. STT: 語音轉文字
    2. LLM: 摘要與路由
    3. Notion: 建立頁面
    4. Line: 推播通知
    """
    try:
        logger.info(f"Processing voice note: {file_path}")
        
        # 1. STT
        stt_service = STTService()
        transcript = stt_service.transcribe(file_path)
        logger.info(f"Transcript: {transcript[:100]}...")
        
        # 2. Notion: 同步可用頁面
        notion_service = NotionService()
        available_pages = notion_service.sync_available_pages()
        
        # 3. LLM: 分析與路由
        llm_service = LLMService()
        analysis = llm_service.summarize_and_route(transcript, available_pages)
        logger.info(f"Analysis: {analysis}")
        
        # 4. Notion: 建立頁面
        target_page_id = analysis.get("target_page_id")
        if not target_page_id or target_page_id == "Inbox":
            # 若無法判斷，使用第一個可用頁面
            target_page_id = available_pages[0]["id"] if available_pages else None
        
        if not target_page_id:
            raise Exception("No available Notion page found")
        
        notion_url = notion_service.create_page(target_page_id, analysis)
        
        # 5. Line: 推播通知
        notification_service = NotificationService()
        notification_service.push_message(analysis["title"], notion_url)
        
        # 6. 清理暫存檔案
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up: {file_path}")
        
        logger.info("Voice note processing completed successfully")
        
        return {
            "status": "success",
            "title": analysis["title"],
            "notion_url": notion_url
        }
        
    except Exception as e:
        logger.error(f"Task failed: {e}", exc_info=True)
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))
