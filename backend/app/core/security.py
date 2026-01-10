"""
Security Utilities
提供資料加密與解密功能，確保傳輸安全性。
"""
import json
from base64 import b64encode, b64decode
from typing import Dict, Any

from cryptography.fernet import Fernet
from app.config import get_settings
from app.core.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)

class TaskSecurity:
    """處理任務酬載的加密與解密"""
    
    _fernet = None

    @classmethod
    def _get_fernet(cls) -> Fernet:
        if cls._fernet is None:
            key = settings.TASK_ENCRYPTION_KEY
            if not key:
                # 若未設定金鑰，在開發模式下自動生成一個（生產環境則需預先設定，否則報錯）
                if settings.DEBUG:
                    logger.warning("TASK_ENCRYPTION_KEY not set, generating a temporary one for DEBUG mode.")
                    key = Fernet.generate_key().decode()
                else:
                    raise ValueError("TASK_ENCRYPTION_KEY must be set in production.")
            
            try:
                cls._fernet = Fernet(key.encode())
            except Exception as e:
                logger.error(f"Invalid TASK_ENCRYPTION_KEY: {e}")
                raise
        return cls._fernet

    @classmethod
    def encrypt_payload(cls, data: Dict[str, Any]) -> str:
        """加密字典為字串"""
        try:
            json_data = json.dumps(data)
            fernet = cls._get_fernet()
            encrypted = fernet.encrypt(json_data.encode())
            return encrypted.decode()
        except Exception as e:
            logger.error(f"Payload encryption failed: {e}")
            raise

    @classmethod
    def decrypt_payload(cls, encrypted_str: str) -> Dict[str, Any]:
        """解密字串為字典"""
        try:
            fernet = cls._get_fernet()
            decrypted = fernet.decrypt(encrypted_str.encode())
            return json.loads(decrypted.decode())
        except Exception as e:
            logger.error(f"Payload decryption failed: {e}")
            raise
