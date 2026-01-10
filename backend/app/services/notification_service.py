"""
Notification Service - Line Messaging API
透過 Line 推播處理結果給使用者
"""
from typing import Optional
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
from app.schemas.context import UserContext, AuthType

settings = get_settings()
logger = get_logger(__name__)


class NotificationService:
    """Line Push Notification Service"""
    
    def __init__(self, context: Optional[UserContext] = None):
        self.is_demo = False
        self.enabled = True
        
        if context and context.type == AuthType.DEMO:
            self.is_demo = True
            if context.line_token and context.line_user_id:
                self.access_token = context.line_token
                self.user_id = context.line_user_id
            else:
                # Demo mode: Line is optional. If missing, just disable.
                logger.warning("Demo mode: No Line credentials provided, notifications disabled.")
                self.enabled = False
        else:
            self.access_token = settings.LINE_CHANNEL_ACCESS_TOKEN
            self.user_id = settings.LINE_USER_ID

        if self.enabled:
            configuration = Configuration(
                access_token=self.access_token
            )
            self.api_client = ApiClient(configuration)
            self.messaging_api = MessagingApi(self.api_client)
            logger.info(f"Line Messaging API initialized (Mode: {'Demo' if self.is_demo else 'Admin'})")
        else:
            logger.info("Line Messaging API skipped (Disabled)")
    
    def push_message(self, title: str, notion_url: str) -> None:
        """
        推播訊息給使用者
        
        Args:
            title: 筆記標題
            notion_url: Notion 頁面連結
        """
        if not self.enabled:
            logger.info("Notification disabled, skipping push_message")
            return

        try:
            message = f"✅ 您的筆記已建立！\n\n📝 標題：{title}\n\n🔗 {notion_url}"
            
            self.messaging_api.push_message(
                PushMessageRequest(
                    to=self.user_id,
                    messages=[TextMessage(text=message)]
                )
            )
            
            logger.info(f"Pushed notification to Line user: {self.user_id}")
            
        except Exception as e:
            logger.error(f"Failed to push Line message: {e}", exc_info=True)
            # We don't want to fail the whole task just because of notification
            pass
