"""
Voice Note Routes
處理語音筆記上傳 API
"""
import os
import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException, Request, Depends
from app.schemas.voice_note import VoiceNoteResponse
from app.worker.tasks import process_voice_note
from app.core.logger import get_logger
from app.config import get_settings
from app.services.audio_validator import validate_audio_format, validate_file_size, MAX_FILE_SIZE
from app.core.dependencies import get_user_context
from app.schemas.context import UserContext
from app.core.security import TaskSecurity

logger = get_logger(__name__)
settings = get_settings()

router = APIRouter(
    prefix="/api/v1",
    tags=["Voice Note"]
)


@router.post("/note", response_model=VoiceNoteResponse, status_code=202)
async def upload_voice_note(
    audio: UploadFile = File(..., description="音訊檔案"),
    context: UserContext = Depends(get_user_context)
):
    """
    上傳語音筆記 (標準 multipart/form-data)

    功能步驟:
    - 接收音訊檔案
    - 存入暫存目錄
    - 發送至 Celery Queue
    - 立即回傳 202 Accepted
    
    適用場景:
    - cURL 測試與開發
    - 未來的 Web 介面整合
    - 第三方整合（需搭配 OAuth/JWT）
    
    安全機制:
    - 檔案大小限制 (25MB)
    - Magic Number 格式驗證
    - 身份驗證: # TODO（未來整合 OAuth）
    """
    try:
        # 📦 讀取檔案內容
        content = await audio.read()
        
        if not content:
            raise HTTPException(status_code=400, detail="未收到音訊資料")
        
        # 📏 檢查檔案大小
        validate_file_size(content)
        
        # 🔍 驗證音訊格式 (Magic Number)
        file_ext = validate_audio_format(content)
        
        # 💾 儲存檔案
        file_id = str(uuid.uuid4()) # 防止路徑注入攻擊
        file_path = f"/data/{file_id}{file_ext}"
        
        os.makedirs("/data", mode=0o700, exist_ok=True)  # 限制目錄權限為僅擁有者可存取
        with open(file_path, "wb") as f:
            f.write(content)
        
        logger.info(f"Audio file saved (standard): {file_path}, Auth: {context.type}")
        
        # 🚀 發送 Celery 任務 (傳遞加密後的 Context)
        encrypted_context = TaskSecurity.encrypt_payload(context.model_dump())
        task = process_voice_note.delay(file_path, encrypted_context)
        logger.info(f"Task enqueued: {task.id}")
        
        return VoiceNoteResponse(
            message="已收到，開始處理",
            task_id=task.id
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload (standard) failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="上傳失敗")


@router.post("/note/ios", response_model=VoiceNoteResponse, status_code=202)
async def upload_voice_note_ios(
    request: Request,
    context: UserContext = Depends(get_user_context)
):
    """
    上傳語音筆記 (iOS Shortcuts 專用 / BYOK 支援)
    
    此端點接收原始二進位資料，適用於 iOS Shortcuts 的 File 上傳模式。
    
    安全機制:
    - API Key 驗證 (X-API-Key header)
    - 檔案大小限制 (25MB)
    - Magic Number 格式驗證
    
    Shortcuts 設定:
    1. Request Body 選擇 File
    2. Headers 加入 X-API-Key
    3. Method 設為 POST
    
    詳細設定請參考: docs/SIRI_INTEGRATION_DEMO.md 或 docs/SIRI_INTEGRATION_ADMIN.md
    """
    try:
        # 📦 讀取檔案內容
        content = await request.body()
        
        if not content:
            raise HTTPException(status_code=400, detail="未收到音訊資料")
        
        # 📏 檢查檔案大小
        validate_file_size(content)
        
        # 🔍 驗證音訊格式 (Magic Number)
        file_ext = validate_audio_format(content)
        
        # 💾 儲存檔案
        file_id = str(uuid.uuid4())  # 防止路徑注入攻擊
        file_path = f"/data/{file_id}{file_ext}"
        
        os.makedirs("/data", mode=0o700, exist_ok=True)  # 限制目錄權限為僅擁有者可存取
        with open(file_path, "wb") as f:
            f.write(content)
        
        logger.info(f"Audio file saved (iOS): {file_path}, Auth: {context.type}")
        
        # 🚀 發送 Celery 任務 (傳遞加密後的 Context)
        encrypted_context = TaskSecurity.encrypt_payload(context.model_dump())
        task = process_voice_note.delay(file_path, encrypted_context)
        logger.info(f"Task enqueued: {task.id}")
        
        return VoiceNoteResponse(
            message="已收到，開始處理",
            task_id=task.id
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload (iOS) failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="上傳失敗")
