"""
FastAPI Main Application
Note-Goblin Backend
"""

import os

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.core.logger import get_logger
from app.core.request_limits import RequestBodyLimitMiddleware
from app.routes import capture_management, drive_oauth, line_webhook, voice_note

logger = get_logger(__name__)
settings = get_settings()

PORTAL_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
}


class PortalStaticFiles(StaticFiles):
    """HTML 一律走有 CSP 的正式入口，避免 raw static 路徑繞過回應防護。"""

    async def get_response(self, path, scope):
        target = {
            "demo.html": "/demo",
            "drive_setup.html": "/oauth/google/setup",
            "drive_complete.html": "/oauth/google/complete",
        }.get(path)
        if target:
            return RedirectResponse(target, status_code=307, headers=PORTAL_HEADERS)
        return await super().get_response(path, scope)


app = FastAPI(title=settings.APP_NAME, description="語音筆記自動化系統", version="0.5.0")

# Trusted Host Middleware
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts_list)


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
        err_copy = {"loc": error["loc"], "type": error["type"], "msg": "Invalid request field"}
        errors.append(err_copy)
    logger.warning("Request validation failed (%s fields)", len(errors))
    return JSONResponse(
        status_code=422,
        content={"detail": errors},
    )


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# multipart framing 額外保留 64 KiB；檔案實際內容另以 CAPTURE_MAX_BYTES 驗證。
app.add_middleware(RequestBodyLimitMiddleware, max_bytes=settings.CAPTURE_MAX_BYTES + 64 * 1024)
app.add_middleware(
    drive_oauth.OAuthOwnerGuard,
    api_key=settings.SIRI_API_KEY,
    redirect_uri=settings.DRIVE_OAUTH_REDIRECT_URI,
)

# 註冊路由
app.include_router(voice_note.router)
app.include_router(line_webhook.router)
app.include_router(capture_management.router)
app.include_router(drive_oauth.router)

# Mount Static Files (for Demo Page)
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", PortalStaticFiles(directory=static_dir), name="static")


@app.get("/")
async def root():
    """直接顯示 Demo 頁面 (或跳轉)"""
    static_file = os.path.join(os.path.dirname(__file__), "static", "demo.html")
    if os.path.exists(static_file):
        return FileResponse(static_file, headers=PORTAL_HEADERS)
    return {"status": "ok", "service": settings.APP_NAME}


@app.get("/demo")
async def demo_page():
    """Demo 頁面直達路徑"""
    static_file = os.path.join(os.path.dirname(__file__), "static", "demo.html")
    return FileResponse(static_file, headers=PORTAL_HEADERS)


@app.get("/health")
async def health():
    """健康檢查"""
    return {"status": "healthy"}
