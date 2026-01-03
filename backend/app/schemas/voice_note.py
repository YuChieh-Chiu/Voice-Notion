"""
Voice Note Schema
API 請求/回應的資料結構
"""
from pydantic import BaseModel


class VoiceNoteResponse(BaseModel):
    """語音筆記回應"""
    message: str
    task_id: str
