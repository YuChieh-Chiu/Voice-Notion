"""
Notification Service - Line Messaging API
透過 Line 推播處理結果給使用者
"""
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    PushMessageRequest,
    TextMessage
)
from app.config import get_settings
from app.core.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)


class NotificationService:
    """Line Push Notification Service"""
    
    def __init__(self):
        configuration = Configuration(
            access_token=settings.LINE_CHANNEL_ACCESS_TOKEN
        )
        self.api_client = ApiClient(configuration)
        self.messaging_api = MessagingApi(self.api_client)
        logger.info("Line Messaging API initialized")
    
    def push_message(self, title: str, notion_url: str) -> None:
        """
        推播訊息給使用者
        
        Args:
            title: 筆記標題
            notion_url: Notion 頁面連結
        """
        try:
            message = f"✅ 您的筆記已建立！\n\n📝 標題：{title}\n\n🔗 {notion_url}"
            
            self.messaging_api.push_message(
                PushMessageRequest(
                    to=settings.LINE_USER_ID,
                    messages=[TextMessage(text=message)]
                )
            )
            
            logger.info(f"Pushed notification to Line user: {settings.LINE_USER_ID}")
            
        except Exception as e:
            logger.error(f"Failed to push Line message: {e}", exc_info=True)
            raise
