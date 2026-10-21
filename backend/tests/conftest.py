"""測試只使用假憑證、暫存資料及替身，不載入開發者的服務設定。"""

import os

from cryptography.fernet import Fernet

os.environ.update(
    {
        "GEMINI_API_KEY": "test-gemini",
        "NOTION_TOKEN": "test-notion",
        "LINE_CHANNEL_ACCESS_TOKEN": "test-line",
        "LINE_USER_ID": "test-owner",
        "LINE_CHANNEL_SECRET": "test-channel",
        "SIRI_API_KEY": "test-personal",
        "TASK_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "CAPTURE_DESTINATION": "notion",
        "DRIVE_CLIENT_ID": "",
        "DRIVE_CLIENT_SECRET": "",
        "DRIVE_REFRESH_TOKEN": "",
        "DRIVE_FOLDER_ID": "",
        "JEV_API_KEY": "",
    }
)
