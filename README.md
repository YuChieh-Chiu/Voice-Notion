<div align="center">
  <img src="assets/voice-notion-logo.png" alt="Voice-Notion Logo" width="200">
</div>

# Voice-Notion 語音筆記助理

一個整合語音助理、FastAPI、Celery 與 Notion 的語音筆記自動化系統。

## 功能特點

- **語音輸入**: 透過 Siri 或其他語音助理錄音上傳
- **安全機制** 🔒:
  - Magic Number 驗證（檔案簽章檢查）
  - 檔案大小限制（25MB）
  - API Key 驗證（iOS 端點）
- **自動轉錄**: Faster-Whisper (CPU) 進行 STT
- **兩階段 AI**:
  - Stage 1: 路由判斷（操作類型、筆記類型、目標頁面）
  - Stage 2: 模板化摘要生成（meeting/idea/todo/general）
- **智慧整合**: Create 新頁面或 Append 到現有頁面
- **即時通知**: 完成後透過 Line 推播

## 技術架構

- **Backend**: FastAPI + Celery + Redis
- **STT**: Faster-Whisper (Small/CPU)
- **LLM**: Gemini Flash Lite（兩階段架構）
  - 路由判斷: Structured Output (temp=0.0)
  - 摘要生成: 模板驅動 (temp=1.0)
- **Notification**: Line Messaging API
- **Storage**: Notion API

## 部署建議 (Production Deployment)

### 1. 硬體需求與記憶體配置
由於本專案包含 AI 語音轉錄模型 (Faster-Whisper)，**極度建議**伺服器至少具備 **4GB RAM**。
若您使用 **1GB RAM** 的入門級主機 (如 Linode Nanode)，**必須**設定 Swap 虛擬記憶體以防止 OOM (Out Of Memory) 導致 Worker 崩潰。

**設定 4GB Swap 指令 (Linux):**
```bash
# 1. 關閉目前的 swap
sudo swapoff -a

# 2. 建立 4GB swap 檔案
sudo fallocate -l 4G /swapfile

# 3. 設定權限 (僅 root 可讀寫)
sudo chmod 600 /swapfile

# 4. 格式化並啟用
sudo mkswap /swapfile
sudo swapon /swapfile

# 5. 確認結果
free -h
```

### 2. 環境變數
詳細設定請參考 `docs/DEPLOYMENT_GUIDE_ADMIN.md`，生產環境重點檢查：
- `ALLOWED_HOSTS`: 務必設定正確的網域名稱，避免 Host Header 攻擊。
- `GEMINI_API_KEY`: 確保 Key 有足夠的 Quota。

## 🎬 快速體驗 (Quick Start Demo)

如果您只是想體驗功能，無需部署伺服器：
1.  準備您的 Google Gemini API Key 與 Notion Token。
2. 前往我們的 [展示網頁 (Demo Page)](https://voice-notion.jacktoholiday.uk/demo)。
3. 按照 **[試用者全指南 (Demo Guide)](./docs/DEMO_GUIDE.md)** 快速完成設定。

---

## 🛠️ 自行部署 (Self-Hosted Admin)

如果您希望建構專屬的私人系統，請遵循以下步驟：

### 1. 部署指南
詳細設定請參考 **[管理者部署指南 (Admin Guide)](./docs/DEPLOYMENT_GUIDE_ADMIN.md)**，重點包含：
- `ENVIRONMENT VARIABLES`: 伺服器端的環境變數設定。
- `SIRI_API_KEY`: 您的個人 iOS 捷徑驗證碼。

### 2. 環境變數
```bash
cp .env.example .env
# 生成管理員專用 API Key (SIRI_API_KEY)
openssl rand -hex 32 
```

### 3. Siri 捷徑整合
參考 **[Siri 完整整合指南 (管理員版)](./docs/SIRI_INTEGRATION_ADMIN.md)** 完成手機端設定。

### 4. 啟動服務

```bash
docker-compose up --build
```

### 5. 測試 API

**標準端點**（無需 API Key）：
```bash
curl -X POST http://localhost:8000/api/v1/note \
  -F "audio=@test.m4a"
```

**iOS 端點**（需要 API Key）：
```bash
curl -X POST http://localhost:8000/api/v1/note/ios \
  -H "X-API-Key: your-api-key" \
  --data-binary @test.m4a
```

詳細的 Siri 整合設定請參考 [docs/SIRI_INTEGRATION_ADMIN.md](docs/SIRI_INTEGRATION_ADMIN.md)

## 專案結構

```
backend/
├── app/
│   ├── main.py           # FastAPI 入口
│   ├── config.py         # 環境變數
│   ├── core/             # 核心模組
│   ├── prompts/          # LLM Prompts & Templates ✨
│   │   ├── routing.py    # 路由判斷 prompt
│   │   └── templates/    # meeting/idea/todo/general (詳見 [docs/PROMPT_TEMPLATES.md](docs/PROMPT_TEMPLATES.md))
│   ├── routes/           # API 路由
│   ├── schemas/          # Pydantic Schema
│   ├── services/         # 業務邏輯
│   │   └── audio_validator.py  # 音訊驗證服務 🔒
│   └── worker/           # Celery Tasks
├── Dockerfile.web        # Web 容器
├── Dockerfile.worker     # Worker 容器
└── pyproject.toml
```

## 開發說明

- Web Container: 輕量，不包含 faster-whisper
- Worker Container: 包含 STT 模型與 ffmpeg
- 共用 codebase，透過不同 Dockerfile 達成分離

## License

本專案採用 **GNU Affero General Public License v3.0 (AGPL-3.0)** 進行授權，詳細內容請參閱 [LICENSE](LICENSE) 檔案。
