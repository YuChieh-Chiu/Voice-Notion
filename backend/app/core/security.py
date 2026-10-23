"""持久化任務 context 加密；web 與 worker 必須使用同一個金鑰。"""

import json

from cryptography.fernet import Fernet

from app.config import get_settings

settings = get_settings()


class TaskSecurity:
    _fernet = None

    @classmethod
    def _get_fernet(cls) -> Fernet:
        if cls._fernet is None:
            if not settings.TASK_ENCRYPTION_KEY:
                raise ValueError("TASK_ENCRYPTION_KEY must be configured in web and worker")
            cls._fernet = Fernet(settings.TASK_ENCRYPTION_KEY.encode())
        return cls._fernet

    @classmethod
    def encrypt_payload(cls, data: dict) -> str:
        return cls._get_fernet().encrypt(json.dumps(data).encode()).decode()

    @classmethod
    def decrypt_payload(cls, encrypted_str: str) -> dict:
        return json.loads(cls._get_fernet().decrypt(encrypted_str.encode()))
