"""
Dependency Injection
依賴注入配置
"""
import json
from typing import Optional, Dict
from fastapi import Header, Request, HTTPException, Depends
from app.services.stt_service import STTService
from app.services.llm_service import LLMService
from app.services.notion_service import NotionService
from app.services.notification_service import NotificationService
from app.schemas.context import UserContext, AuthType
from app.config import get_settings
from app.core.rate_limit import RateLimiter
from app.core.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()

# 單一 RateLimiter 實例 (每小時 3 次)
demo_rate_limiter = RateLimiter(times=3, hours=1)


async def get_user_context(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_voice_notion_config: Optional[str] = Header(None, alias="X-Voice-Notion-Config"),
) -> UserContext:
    """
    實作智慧身份驗證與上下文注入
    
    1. 若帶有 X-API-Key 且匹配 -> Admin 模式
    2. 若帶有 X-Voice-Notion-Config -> 解析 JSON 並進入 Demo 模式 (BYOK)
    3. 否則拋出 401
    """
    
    # Path A: Admin Intent
    if x_api_key is not None:
        if x_api_key == settings.SIRI_API_KEY:
            return UserContext(
                type=AuthType.ADMIN,
                ip_address=request.client.host
            )
        else:
            raise HTTPException(status_code=401, detail="Invalid Admin API Key")

    # Path B: Demo Intent (BYOK via Single Config Header)
    if x_voice_notion_config:
        try:
            config = json.loads(x_voice_notion_config)
            
            # 嚴格從 JSON 中提取必要欄位
            gemini_key = config.get("X-Gemini-Api-Key")
            notion_token = config.get("X-Notion-Token")
            line_token = config.get("X-Line-Token")
            line_user_id = config.get("X-Line-User-ID")
            
            if gemini_key and notion_token:
                await demo_rate_limiter(request)
                return UserContext(
                    type=AuthType.DEMO,
                    gemini_key=gemini_key,
                    notion_token=notion_token,
                    line_token=line_token,
                    line_user_id=line_user_id,
                    ip_address=request.client.host
                )
            else:
                logger.warning(f"Missing required fields in config from {request.client.host}")
                raise HTTPException(status_code=400, detail="Config JSON missing required Gemini or Notion keys")
                
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in X-Voice-Notion-Config from {request.client.host}")
            raise HTTPException(status_code=400, detail="Invalid configuration format (JSON required)")

    # No valid authentication provided
    raise HTTPException(
        status_code=401, 
        detail="Authentication required. Provide X-Voice-Notion-Config or Admin Key."
    )


async def get_stt_service() -> STTService:
    """取得 STT Service"""
    return STTService()


async def get_llm_service(
    context: UserContext = Depends(get_user_context)
) -> LLMService:
    """取得 LLM Service (帶有 Context)"""
    return LLMService(context=context)


async def get_notion_service(
    context: UserContext = Depends(get_user_context)
) -> NotionService:
    """取得 Notion Service (帶有 Context)"""
    return NotionService(context=context)


async def get_notification_service(
    context: UserContext = Depends(get_user_context)
) -> NotificationService:
    """取得 Notification Service (帶有 Context)"""
    return NotificationService(context=context)
