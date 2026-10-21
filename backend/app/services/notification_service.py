"""LINE 完成通知，使用穩定 retry key 避免通知重試重複推播。"""

import uuid

from app.config import get_settings
from app.schemas.context import AuthType, UserContext
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
    ) -> None:
        if not self.enabled:
            return
        status = "原檔已保存，未解析" if original_only else "完整原文與摘要已保存"
        text = f"✅ {status}\n{title}\n{capture_id or ''}\n{url}\n雲端保存成功；本機同步狀態請於 Drive 確認。"
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
                f"⚠️ {capture_id} 原檔已保存至 Drive，文字處理尚未完成。請用 /files {capture_id} 查詢；目前不能視為完整筆記。",
                key="partial:" + capture_id,
            )
