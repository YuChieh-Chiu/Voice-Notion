"""
Audio Validation Service
音訊檔案驗證服務
"""
from fastapi import HTTPException

# 檔案大小限制
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB (支援約 20 分鐘高品質錄音)


def validate_audio_format(content: bytes) -> str:
    """
    驗證音訊檔案格式 (Magic Number 驗證)
    
    Magic Number 是檔案開頭的固定位元組序列，用於識別檔案真實格式。
    此方法比檢查副檔名或 Content-Type 更可靠，因為這些都可以被偽造。
    
    支援格式:
    - M4A: 00 00 00 ?? 66 74 79 70 4D 34 41 (ftyp 之後是 M4A)
    - WAV: 52 49 46 46 (RIFF)
    - MP3: FF FB / FF F3 / FF F2 (MPEG Audio Frame Sync)
    
    Args:
        content: 檔案二進位內容
        
    Returns:
        str: 驗證後的副檔名 (.m4a, .wav, .mp3)
        
    Raises:
        HTTPException: 若格式不支援或檔案損壞
        
    參考文件: docs/AUDIO_FORMAT_VALIDATION.md
    """
    if len(content) < 11:
        raise HTTPException(
            status_code=400,
            detail="檔案過小，可能已損壞"
        )
    
    # M4A 格式檢查 (跳過前 4 bytes 長度標記)
    if content[4:11] == b'ftypM4A':
        return '.m4a'
    
    # WAV 格式檢查 (RIFF header)
    if content.startswith(b'RIFF') and b'WAVE' in content[:12]:
        return '.wav'
    
    # MP3 格式檢查 (MPEG Frame Sync)
    if content[:2] in [b'\xff\xfb', b'\xff\xf3', b'\xff\xf2']:
        return '.mp3'
    
    # 不支援的格式
    raise HTTPException(
        status_code=400,
        detail="不支援的音訊格式 (僅支援 M4A/WAV/MP3)"
    )


def validate_file_size(content: bytes) -> None:
    """
    驗證檔案大小
    
    Args:
        content: 檔案二進位內容
        
    Raises:
        HTTPException: 若檔案超過大小限制
    """
    file_size = len(content)
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"檔案過大 (最大 {MAX_FILE_SIZE // 1024 // 1024}MB)"
        )
