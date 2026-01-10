"""
Application Configuration
使用 Pydantic Settings 管理環境變數
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """應用程式設定"""
    
    # Celery
    REDIS_URL: str = "redis://redis:6379/0"
    
    # Gemini API
    GEMINI_API_KEY: str
    
    # Notion API
    NOTION_TOKEN: str
    NOTION_DATABASE_ID: str = ""  # Optional: 預設資料庫 ID
    
    # Line Messaging API
    LINE_CHANNEL_ACCESS_TOKEN: str
    LINE_USER_ID: str  # 推播目標使用者 ID
    
    # Siri Integration
    SIRI_API_KEY: str = ""  # iOS Shortcuts API 驗證金鑰
    
    # Application
    APP_NAME: str = "Voice-Notion"
    DEBUG: bool = False
    
    # Security
    ALLOWED_HOSTS: list[str] = ["localhost", "127.0.0.1", "voice-notion.jacktoholiday.uk"]
    BACKEND_CORS_ORIGINS: list[str] = ["*"]
    TASK_ENCRYPTION_KEY: str = ""  #用於加密 Celery 任務酬載的 Fernet 金鑰
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


def get_settings() -> Settings:
    """取得設定實例"""
    return Settings()
