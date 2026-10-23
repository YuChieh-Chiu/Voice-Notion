"""LINE 固定官方端點、有限下載及去重推播。"""

import re
import uuid

import httpx

from app.config import Settings


class LineClient:
    def __init__(self, settings: Settings, *, transport=None):
        self.settings, self.transport = settings, transport

    def client(self):
        if not self.settings.LINE_CHANNEL_ACCESS_TOKEN:
            raise ValueError("LINE access token is required")
        return httpx.Client(
            headers={"Authorization": "Bearer " + self.settings.LINE_CHANNEL_ACCESS_TOKEN},
            timeout=self.settings.MODEL_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        )

    def download(self, message_id: str) -> bytes:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", message_id):
            raise ValueError("Invalid LINE message ID")
        with (
            self.client() as client,
            client.stream(
                "GET", "https://api-data.line.me/v2/bot/message/" + message_id + "/content"
            ) as response,
        ):
            response.raise_for_status()
            if int(response.headers.get("content-length", "0")) > self.settings.CAPTURE_MAX_BYTES:
                raise ValueError("LINE content exceeds limit")
            content = bytearray()
            for chunk in response.iter_bytes():
                if len(content) + len(chunk) > self.settings.CAPTURE_MAX_BYTES:
                    raise ValueError("LINE content exceeds limit")
                content.extend(chunk)
            if not content:
                raise ValueError("LINE content is empty")
            return bytes(content)

    def show_loading(self) -> bool:
        """僅顯示手機端暫時動畫；短逾時及失敗都不阻擋正式操作。"""
        try:
            with self.client() as client:
                response = client.post(
                    "https://api.line.me/v2/bot/chat/loading/start",
                    json={"chatId": self.settings.LINE_USER_ID, "loadingSeconds": 60},
                    timeout=2.0,  # 動畫是輔助回饋，不使用完整模型逾時預算。
                )
                return response.status_code == 202
        except (httpx.HTTPError, ValueError):
            return False

    def push(self, text: str, *, key: str, actions: list | None = None) -> None:
        message = {"type": "text", "text": text[:4500]}
        if actions:
            message["quickReply"] = {"items": [{"type": "action", "action": a} for a in actions]}
        retry_key = str(uuid.uuid5(uuid.NAMESPACE_URL, "voice-notion:" + key))
        with self.client() as client:
            response = client.post(
                "https://api.line.me/v2/bot/message/push",
                json={"to": self.settings.LINE_USER_ID, "messages": [message]},
                headers={"X-Line-Retry-Key": retry_key},
            )
            if response.status_code == 409 and response.headers.get("x-line-accepted-request-id"):
                return
            response.raise_for_status()
