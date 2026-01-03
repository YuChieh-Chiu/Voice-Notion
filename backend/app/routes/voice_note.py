"""
Voice Note Routes
處理語音筆記上傳 API
"""
import os
import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException
from app.schemas.voice_note import VoiceNoteResponse
from app.worker.tasks import process_voice_note
from app.core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/v1",
    tags=["Voice Note"]
)


@router.post("/note", response_model=VoiceNoteResponse, status_code=202)
async def upload_voice_note(
    audio: UploadFile = File(..., description="音訊檔案")
):
    """
    上傳語音筆記
    
    - 接收音訊檔案
    - 存入暫存目錄
    - 發送至 Celery Queue
    - 立即回傳 202 Accepted
    """
    try:
        # 生成唯一檔名
        file_id = str(uuid.uuid4())
        file_ext = os.path.splitext(audio.filename)[1]
        file_path = f"/data/{file_id}{file_ext}"
        
        # 儲存檔案
        with open(file_path, "wb") as f:
            content = await audio.read()
            f.write(content)
        
        logger.info(f"Audio file saved: {file_path}")
        
        # 發送 Celery 任務
        task = process_voice_note.delay(file_path)
        
        logger.info(f"Task enqueued: {task.id}")
        
        return VoiceNoteResponse(
            message="已收到，開始處理",
            task_id=task.id
        )
        
    except Exception as e:
        logger.error(f"Upload failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="上傳失敗")
