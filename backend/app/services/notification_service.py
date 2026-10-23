"""LINE 完成通知，使用穩定 retry key 避免通知重試重複推播。"""

import uuid

from app.config import get_settings
from app.schemas.context import AuthType, UserContext
from app.services import line_messages
from app.services.line_client import LineClient

settings = get_settings()


class NotificationService:
    def __init__(self, context: UserContext | None = None):
        config = settings
        self.personal = not context or context.type != AuthType.DEMO
        if context and context.type == AuthType.DEMO:
            config = settings.model_copy(
                update={
                    "LINE_CHANNEL_ACCESS_TOKEN": context.line_token or "",
                    "LINE_USER_ID": context.line_user_id or "",
                }
            )
        self.enabled = bool(config.LINE_CHANNEL_ACCESS_TOKEN and config.LINE_USER_ID)
        self.client = LineClient(config)

    def push_message(
        self,
        title: str,
        url: str,
        *,
        capture_id: str | None = None,
        original_only: bool = False,
        destination: str = "drive",
        reviewed: bool = False,
    ) -> None:
        if not self.enabled:
            return
        text = line_messages.completion(
            title,
            url,
            capture_id=capture_id,
            original_only=original_only,
            destination=destination if self.personal else "notion",
            personal=self.personal,
            reviewed=reviewed,
        )
        actions = []
        if capture_id and self.personal:
            actions.append({"type": "message", "label": "查看檔案", "text": "/files " + capture_id})
            if not original_only:
                actions.append(
                    {
                        "type": "postback",
                        "label": "確認全文與摘要正確",
                        "data": "review:" + capture_id,
                    }
                )
        self.client.push(text, key="complete:" + (capture_id or uuid.uuid4().hex), actions=actions)

    def push_partial(self, capture_id: str) -> None:
        if self.enabled:
            self.client.push(
                line_messages.partial(
                    capture_id,
                    personal=self.personal,
                    destination="drive" if self.personal else "notion",
                ),
                key="partial:" + capture_id,
            )
