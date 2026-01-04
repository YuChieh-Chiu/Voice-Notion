"""
FastAPI Main Application
Siri-Notion Backend
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes import voice_note
from app.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    description="語音筆記自動化系統",
    version="0.2.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
