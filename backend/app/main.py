"""
FastAPI Main Application
Siri-Notion Backend
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes import voice_note
from app.config import get_settings

# Unified Request Validation Handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from app.core.logger import get_logger

logger = get_logger(__name__)

settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    description="語音筆記自動化系統",
    version="0.3.0"
)

# Trusted Host Middleware
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.ALLOWED_HOSTS
)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    """
    統一處理 Pydantic 驗證錯誤
    
    提供比 FastAPI 預設更詳細的錯誤診斷資訊，
    協助前端或 API 客戶端快速定位問題。
    """
    # Convert errors to JSON-serializable format
    errors = []
    for error in exc.errors():
        err_copy = {k: str(v) if not isinstance(v, (str, int, float, bool, list, dict, type(None))) else v for k, v in error.items()}
        errors.append(err_copy)
    logger.warning(f"Request validation failed: {errors}")
    logger.warning(f"Request content-type: {request.headers.get('content-type')}")
    return JSONResponse(
        status_code=422,
        content={"detail": errors},
    )


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 註冊路由
app.include_router(voice_note.router)


@app.get("/")
async def root():
    """Health Check"""
    return {"status": "ok", "service": "Siri-Notion Backend"}


@app.get("/health")
async def health():
    """健康檢查"""
    return {"status": "healthy"}
