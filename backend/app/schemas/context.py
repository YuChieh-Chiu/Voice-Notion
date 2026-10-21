from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict


class AuthType(str, Enum):
    ADMIN = "admin"
    DEMO = "demo"


class UserContext(BaseModel):
    """
    使用者連線上下文
    - admin: 使用正確的 X-API-Key
    - demo: 使用 BYOK Headers
    """

    type: AuthType
    # BYOK (Bring Your Own Key) Data
    gemini_key: Optional[str] = None
    notion_token: Optional[str] = None
    line_token: Optional[str] = None
    line_user_id: Optional[str] = None

    # Metadata
    ip_address: Optional[str] = None

    model_config = ConfigDict(frozen=True)
