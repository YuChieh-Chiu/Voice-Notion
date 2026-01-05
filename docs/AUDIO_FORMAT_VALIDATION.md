# 音訊格式驗證技術文件

## 什麼是 Magic Number？

Magic Number（魔術數字）是檔案**開頭的固定位元組序列**，用來識別檔案的真實格式。作業系統和應用程式透過檢查這些特徵位元組來判斷檔案類型，而非僅依賴副檔名（副檔名可以輕易被偽造）。

## 為什麼需要 Magic Number 驗證？

### 安全風險

假設只檢查副檔名或 `Content-Type` header：

```bash
# 攻擊者可以偽造副檔名
echo "malicious script" > fake.m4a

# 也可以偽造 HTTP header
curl -X POST -H "Content-Type: audio/x-m4a" \
  --data-binary @virus.exe \
  https://your-api.com/api/v1/note/ios
```

若 API 未驗證檔案真實性，可能導致：
- ✗ Celery Worker 處理惡意檔案
- ✗ Faster-Whisper 程式崩潰
- ✗ 檔案系統被注入惡意內容

### Magic Number 提供的保護

- ✓ **檔案真實性驗證**：確保上傳的確實是音訊檔案
- ✓ **防止格式欺騙**：即使副檔名或 header 偽造，也能識別
- ✓ **早期失敗**：在進入處理 pipeline 前就攔截無效檔案

## 支援的音訊格式

### M4A (MPEG-4 Audio)

**Magic Number**: `00 00 00 ?? 66 74 79 70 4D 34 41`

```
偏移量    十六進位                           ASCII
00000000  00 00 00 20 66 74 79 70 4D 34 41  ....ftypM4A
          └─────┬─────┘ └────────┬───────┘
          長度標記(4B)    ftyp + M4A
```

**檢查邏輯**：
- 跳過前 4 bytes（Box Size，可變）
- 檢查第 5-11 bytes 是否為 `ftypM4A`

**常見變體**：
- `ftypM4A ` (有空格)
- `ftypM4V ` (M4V 視訊，應拒絕)

### WAV (Waveform Audio)

**Magic Number**: `52 49 46 46 ... 57 41 56 45`

```
偏移量    十六進位                           ASCII
00000000  52 49 46 46 xx xx xx xx 57 41 56 45  RIFF....WAVE
          └───┬───┘                 └───┬──┘
           RIFF                       WAVE
```

**檢查邏輯**：
- 前 4 bytes 為 `RIFF`
- 第 9-12 bytes 為 `WAVE`

### MP3 (MPEG Audio Layer III)

**Magic Number**: `FF FB` / `FF F3` / `FF F2`

```
偏移量    二進位                              說明
00000000  11111111 111110xx                  MPEG Frame Sync
          └───────┬───────┘
          Frame Sync (11 bits)
```

**檢查邏輯**：
- 前 2 bytes 符合 MPEG 同步模式
- `FF FB`: MPEG-1 Layer III
- `FF F3`: MPEG-2 Layer III
- `FF F2`: MPEG-2.5 Layer III

## 實作說明

### 驗證函式

```python
def validate_audio_format(content: bytes) -> str:
    """驗證音訊格式並回傳副檔名"""
    
    # 最小檔案大小檢查（防止空檔案或損壞檔案）
    if len(content) < 11:
        raise HTTPException(400, "檔案過小，可能已損壞")
    
    # M4A 格式
    if content[4:11] == b'ftypM4A':
        return '.m4a'
    
    # WAV 格式
    if content.startswith(b'RIFF') and b'WAVE' in content[:12]:
        return '.wav'
    
    # MP3 格式
    if content[:2] in [b'\xff\xfb', b'\xff\xf3', b'\xff\xf2']:
        return '.mp3'
    
    # 拒絕不支援的格式
    raise HTTPException(400, "不支援的音訊格式")
```

### 使用範例

```python
# 在端點中使用
content = await request.body()
file_ext = validate_audio_format(content)  # 回傳 '.m4a'
file_path = f"/data/{uuid.uuid4()}{file_ext}"
```

## 故障排除

### 錯誤 400: 檔案過小

**原因**：檔案內容少於 11 bytes

**解決方案**：
1. 檢查錄音是否正常完成
2. 確認網路傳輸未中斷
3. 驗證 Shortcuts 設定正確

### 錯誤 400: 不支援的音訊格式

**原因**：檔案不是 M4A/WAV/MP3

**可能情況**：
- **M4V 視訊檔**：Shortcuts 錄製設定錯誤
- **AAC 裸流**：缺少 M4A 容器
- **其他格式**：FLAC、OGG 等

**解決方案**：
```bash
# 使用 hexdump 檢查檔案頭
hexdump -C audio_file.m4a | head -n 3

# 預期輸出（M4A）：
# 00000000  00 00 00 20 66 74 79 70  4d 34 41 20 |.... ftypM4A |
```

### 錯誤 400: 檔案損壞

**原因**：傳輸過程中檔案損壞

**解決方案**：
1. 重新錄製並上傳
2. 檢查網路連線品質
3. 確認 Shortcuts 正確讀取檔案
