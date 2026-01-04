# Voice-Notion 測試指南 (TESTING GUIDE)

本文件說明如何對 Voice-Notion 系統進行測試，包含單元測試與手動冒煙測試。

## 1. 單元測試 (Unit Testing)

單元測試針對個別服務模組（STT、LLM、Notion）進行驗證，使用 Mock 模擬外部 API 與重量級模型。

### 環境準備
確保已安裝開發依賴：
```bash
cd backend
poetry install --with dev
```

### 執行測試
由於專案使用 Pydantic Settings，執行測試時必須提供必要的環境變數（即使是虛假值）：

```bash
cd backend
GEMINI_API_KEY=test NOTION_TOKEN=test LINE_CHANNEL_ACCESS_TOKEN=test LINE_USER_ID=test PYTHONPATH=. poetry run pytest tests/
```

### 測試涵蓋範圍
- **LLMService**: 路由判斷邏輯、摘要模板生成、異常處理。
- **STTService**: 轉錄介面調用驗證。
- **NotionService**: 頁面搜尋、建立與內容追加。

### Notion 整合驗證腳本
專案提供了一個獨立腳本來驗證 Notion API 連線與權限設定，不需啟動整個 Backend。

```bash
# 確保 .env 已設定 NOTION_TOKEN
cd backend
poetry run python -m scripts.verify_notion_integration
```

**驗證項目**：
1. **Auth**: 檢查 Token 是否有效。
2. **Search**: 列出 Bot 可存取的所有頁面 (確認是否已 Add connections)。
3. **Markdown**: 測試 Markdown 轉 Block 功能 (雖然不寫入 Notion，但會驗證轉換邏輯)。

---

## 2. 手動冒煙測試 (Manual Smoke Test)

冒煙測試用於驗證整個 Pipeline 是否能在真實環境（如 Docker）中通暢運行。

### 前置作業
1. 確保 `.env` 檔案已正確設定真實的 API Keys。
2. 啟動 Docker 容器：
   ```bash
   docker-compose up -d
   ```

### 執行測試
使用提供的測試腳本模擬音訊上傳：

```bash
cd backend
# 請替換 --file 為您的測試音訊路徑 (wav, m4a, mp3)
python scripts/smoke_test.py --file path/to/your_audio.m4a
```

### 驗證步驟
1. **API 回應**: 確認腳本取得 `task_id`。
2. **Worker 日誌**: 檢查 Worker 是否正確轉錄並調用 LLM：
   ```bash
   docker-compose logs -f worker
   ```
3. **Notion**: 確認目標頁面是否出現新筆記。
4. **Line**: 確認手機是否收到包含連結的推播。

---

## 3. 常見問題 (Troubleshooting)

- **ModuleNotFoundError: No module named 'app'**
  - 確保在 `backend` 目錄下執行，且環境變數包含 `PYTHONPATH=.`。
- **ValidationError (Settings)**
  - 測試執行時缺少必要的環境變數，請參考「執行測試」章節。
- **STT 執行過長**
  - 第一次執行時 Faster-Whisper 會下載模型（約 500MB），請耐心等待。
