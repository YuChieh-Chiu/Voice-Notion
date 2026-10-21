"""
Application Configuration
使用 Pydantic Settings 管理環境變數
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CaptureCategory(BaseModel):
    """使用者設定的允許主題；模型不能提供路徑。"""

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    description: str = Field(min_length=1, max_length=500)
    folder_id: str = Field(default="", pattern=r"^[A-Za-z0-9_-]{0,200}$")


class Settings(BaseSettings):
    """應用程式設定"""

    # Celery
    REDIS_URL: str = "redis://redis:6379/0"

    # Gemini API
    GEMINI_API_KEY: str = ""

    # Notion API
    NOTION_TOKEN: str = ""
    NOTION_DATABASE_ID: str = ""  # Optional: 預設資料庫 ID

    # Line Messaging API
    LINE_CHANNEL_ACCESS_TOKEN: str = ""
    LINE_USER_ID: str = ""  # 唯一允許的私人收件及管理使用者
    LINE_CHANNEL_SECRET: str = ""

    # 舊部署維持 Notion；只有啟用 Drive 才要求其 OAuth 設定。
    CAPTURE_DESTINATION: Literal["notion", "drive"] = "notion"
    CAPTURE_DATA_DIR: Path = Path("/data")
    CAPTURE_MAX_BYTES: int = Field(default=25 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    CAPTURE_MAX_TEXT_CHARS: int = Field(default=100_000, ge=1, le=1_000_000)
    CAPTURE_TASK_TIMEOUT_SECONDS: int = Field(default=900, ge=30, le=3600)
    CAPTURE_CLASSIFICATION: Literal["none", "custom"] = "none"
    CAPTURE_ROUTER: Literal["jev", "gemini", "rules"] = "jev"
    CAPTURE_CATEGORIES: list[CaptureCategory] = Field(default_factory=list, max_length=30)
    DRIVE_FOLDER_ID: str = Field(default="", pattern=r"^[A-Za-z0-9_-]{0,200}$")
    DRIVE_CLIENT_ID: str = ""
    DRIVE_CLIENT_SECRET: str = ""
    DRIVE_REFRESH_TOKEN: str = ""
    JEV_API_KEY: str = ""
    MODEL_TIMEOUT_SECONDS: int = Field(default=60, ge=1, le=300)
    DELETION_PREVIEW_SECONDS: int = Field(default=600, ge=30, le=3600)
    DELETION_MAX_FILES: int = Field(default=100, ge=1, le=500)

    # Siri Integration
    SIRI_API_KEY: str = ""  # iOS Shortcuts API 驗證金鑰

    # Application
    APP_NAME: str = "Voice-Notion"
    DEBUG: bool = False

    # Security
    ALLOWED_HOSTS: str = "localhost,127.0.0.1"  # 逗號分隔的允許主機列表，生產環境請加入您的網域
    BACKEND_CORS_ORIGINS: list[str] = ["*"]
    TASK_ENCRYPTION_KEY: str = ""  # 用於加密 Celery 任務酬載的 Fernet 金鑰

    @property
    def allowed_hosts_list(self) -> list[str]:
        """將 ALLOWED_HOSTS 字串轉為列表"""
        return [host.strip() for host in self.ALLOWED_HOSTS.split(",") if host.strip()]

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("CAPTURE_CATEGORIES")
    @classmethod
    def unique_categories(cls, categories: list[CaptureCategory]) -> list[CaptureCategory]:
        if len({category.id for category in categories}) != len(categories):
            raise ValueError("Category IDs must be unique")
        if any(category.id == "unclassified" for category in categories):
            raise ValueError("unclassified is reserved for unmatched captures")
        return categories


def get_settings() -> Settings:
    """取得設定實例"""
    return Settings()
