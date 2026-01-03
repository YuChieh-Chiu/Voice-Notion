"""
Dependency Injection
依賴注入配置
"""
from app.services.stt_service import STTService
from app.services.llm_service import LLMService
from app.services.notion_service import NotionService
from app.services.notification_service import NotificationService


async def get_stt_service() -> STTService:
    """取得 STT Service"""
    return STTService()


async def get_llm_service() -> LLMService:
    """取得 LLM Service"""
    return LLMService()


async def get_notion_service() -> NotionService:
    """取得 Notion Service"""
    return NotionService()


async def get_notification_service() -> NotificationService:
    """取得 Notification Service"""
    return NotionService()
