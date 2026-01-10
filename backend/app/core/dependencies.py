"""
Dependency Injection
依賴注入配置
"""
from typing import Optional
from fastapi import Header, Request, HTTPException, Depends
from app.services.stt_service import STTService
from app.services.llm_service import LLMService
from app.services.notion_service import NotionService
from app.services.notification_service import NotificationService
from app.schemas.context import UserContext, AuthType
from app.config import get_settings
from app.core.rate_limit import RateLimiter

settings = get_settings()

# 單一 RateLimiter 實例 (每小時 3 次)
demo_rate_limiter = RateLimiter(times=3, hours=1)


async def get_user_context(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_gemini_api_key: Optional[str] = Header(None, alias="X-Gemini-Api-Key"),
    x_notion_token: Optional[str] = Header(None, alias="X-Notion-Token"),
    x_line_token: Optional[str] = Header(None, alias="X-Line-Token"),
    x_line_user_id: Optional[str] = Header(None, alias="X-Line-User-ID"),
) -> UserContext:
    """
    實作智慧身份驗證與上下文注入
    
    1. 若帶有 X-API-Key 且匹配 -> Admin 模式
    2. 若不匹配 -> 檢查是否為 Demo 意圖 (帶有 BYOK Headers)
       - 是 -> 執行 Rate Limit，回傳 Demo Context
       - 否 -> 拋出 401
    """
    
    # Path A: Admin Intent
    if x_api_key is not None:
        if x_api_key == settings.SIRI_API_KEY:
            return UserContext(
                type=AuthType.ADMIN,
                ip_address=request.client.host
            )
        else:
            # Explicit Admin Key provided but wrong
            raise HTTPException(status_code=401, detail="Invalid Admin API Key")

    # Path B: Demo Intent (BYOK)
    if x_gemini_api_key and x_notion_token:
        # 執行限流檢查
        await demo_rate_limiter(request)
        
        return UserContext(
            type=AuthType.DEMO,
            gemini_key=x_gemini_api_key,
            notion_token=x_notion_token,
            line_token=x_line_token,
            line_user_id=x_line_user_id,
            ip_address=request.client.host
        )
        
    # No valid authentication provided
    raise HTTPException(
        status_code=401, 
        detail="Authentication required. Provide a valid Admin Key or BYOK Headers."
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
