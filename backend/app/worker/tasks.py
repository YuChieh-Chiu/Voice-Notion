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
    處理語音筆記 Pipeline（兩階段 LLM）
    
    1. STT: 語音轉文字
    2. LLM Stage 1: 路由判斷 (Tree Routing)
    3. LLM Stage 2: 依模板生成摘要
    4. Notion: create_subpage 或 append_to_page
    5. Line: 推播通知
    """
    try:
        logger.info(f"Processing voice note: {file_path}")
        
        # 1. STT
        stt_service = STTService()
        transcript = stt_service.transcribe(file_path)
        logger.info(f"Transcript: {transcript[:100]}...")
        
        # 2. Notion: 同步頁面樹狀結構
        notion_service = NotionService()
        page_tree = notion_service.sync_page_tree()
        
        # 3. LLM Stage 1: 路由判斷
        llm_service = LLMService()
        routing = llm_service.route(transcript, page_tree)
        
        action = routing.get("action")
        target_id = routing.get("target_id")
        title = routing.get("title") or "Untitled Note"
        template_type = routing.get("template_type", "general")
        
        # 4. LLM Stage 2: 依模板生成摘要
        summary_md = llm_service.summarize(transcript, template_type)
        logger.info(f"Summary length: {len(summary_md)} characters")
        
        # 5. Notion: 執行操作
        notion_url = ""
        
        if action == "create":
            # 在 Root 下建立新 Subpage
            # target_id is Root ID
            new_topic_name = routing.get("new_topic_name") or title
            logger.info(f"Action: CREATE subpage '{new_topic_name}' under Root {target_id}")
            
            # 建立 Subpage 並寫入摘要
            result = notion_service.create_subpage(
                parent_id=target_id,
                title=new_topic_name,
                summary_md=summary_md
            )
            notion_url = result["url"]
            
        elif action == "append":
            # 在 Existing Subpage 追加內容
            # target_id is Subpage ID
            logger.info(f"Action: APPEND to subpage {target_id}")
            
            notion_url = notion_service.append_to_page(
                page_id=target_id,
                title=title,
                summary_md=summary_md
            )
            
        else:
             # Fallback logic if needed, but routing schema restricts enum
             raise ValueError(f"Unknown action: {action}")
        
        # 6. Line: 推播通知
        notification_service = NotificationService()
        notification_service.push_message(title, notion_url)
        
        # 7. 清理暫存檔案
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up: {file_path}")
        
        logger.info("Voice note processing completed successfully")
        
        return {
            "status": "success",
            "title": title,
            "notion_url": notion_url
        }
        
    except Exception as e:
        logger.error(f"Task failed: {e}", exc_info=True)
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))
