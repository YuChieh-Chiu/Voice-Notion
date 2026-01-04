# 環境變數設定指南

本文件說明 Voice-Notion 專案所需的所有環境變數設定步驟與官方文件連結。

## 📋 環境變數清單總覽

| 變數名稱 | 類型 | 用途 | 預設值 |
|---------|------|------|--------|
| `GEMINI_API_KEY` | 必要 | Google Gemini API 金鑰（用於 LLM 摘要與路由） | - |
| `NOTION_TOKEN` | 必要 | Notion Integration Token（用於建立頁面） | - |
| `LINE_CHANNEL_ACCESS_TOKEN` | 必要 | Line Messaging API Token（用於推播通知） | - |
| `LINE_USER_ID` | 必要 | Line 推播目標使用者 ID | - |
| `REDIS_URL` | 選用 | Redis 連線 URL（Celery Broker） | `redis://redis:6379/0` |
| `NOTION_DATABASE_ID` | 選用 | 預設 Notion 資料庫 ID | `""` (空字串) |
| `APP_NAME` | 選用 | 應用程式名稱 | `Voice-Notion` |
| `DEBUG` | 選用 | 除錯模式 | `false` |

---

## 🚀 快速開始

### 步驟 1：複製範本檔案
```bash
cp .env.example .env
```

### 步驟 2：依照下方說明填入各項環境變數
在 `.env` 檔案中填入真實值，請勿將 `.env` 提交至版本控制。

---

## 🔑 必要環境變數

### 1. GEMINI_API_KEY

**用途**：用於呼叫 Google Gemini API，執行語音筆記摘要與智慧路由判斷。

#### 取得步驟

1. 前往 [Google AI Studio](https://aistudio.google.com/app/apikey)
2. 使用 Google 帳號登入
3. 點擊「Get API Key」或「建立 API 金鑰」
4. 選擇「Create API key in new project」或使用現有專案
5. 複製產生的 API Key

#### 官方文件
- **API Key 取得指南**: https://ai.google.dev/gemini-api/docs/api-key
- **Gemini API 快速開始**: https://ai.google.dev/gemini-api/docs/quickstart

#### 配置範例
```bash
GEMINI_API_KEY=AIzaSyA1234567890abcdefghijklmnopqrstuv
```

---

### 2. NOTION_TOKEN

**用途**：用於呼叫 Notion API，建立頁面並儲存語音筆記內容。

#### 取得步驟

1. 前往 [Notion Integrations](https://www.notion.so/my-integrations)
2. 點擊「+ New integration」
3. 填寫 Integration 資訊：
   - **Name**: Voice-Notion Bot（或自訂名稱）
   - **Associated workspace**: 選擇目標工作區
   - **Capabilities**: 勾選 `Read content`、`Update content`、`Insert content`
4. 點擊「Submit」建立 Integration
5. 複製「Internal Integration Token」（格式：`ntn_xxxxxxxxx`）
6. **重要**：在 Notion 頁面或資料庫設定中，點擊右上角 `...` → `Add connections` → 選擇剛建立的 Integration，授予存取權限

#### 官方文件
- **建立 Integration**: https://developers.notion.com/docs/create-a-notion-integration
- **授權頁面存取**: https://developers.notion.com/docs/create-a-notion-integration#give-your-integration-page-permissions
- **API 參考**: https://developers.notion.com/reference/intro

#### 配置範例
```bash
NOTION_TOKEN=ntn_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
```

#### 常見問題
- **Q: 出現 `object not found` 錯誤？**
  - A: 確認已在目標頁面授予 Integration 存取權限（步驟 6）

---

### 3. LINE_CHANNEL_ACCESS_TOKEN

**用途**：用於透過 Line Messaging API 推播通知給使用者。

#### 取得步驟

> [!IMPORTANT]
> **官方流程變更 (2024/09)**：現在無法直接在 LINE Developers Console 建立 Messaging API Channel。請遵循以下步驟透過「官方帳號管理後台」建立。

**步驟 A：建立 LINE 官方帳號 (OA)**
1. 前往 [LINE Business ID 註冊頁面](https://account.line.biz/signup)
2. 選擇「建立 LINE 官方帳號」並填寫必要資訊
3. 完成後，登入 [LINE Official Account Manager](https://manager.line.biz/)

**步驟 B：啟用 Messaging API**
1. 在 OA Manager 選擇剛建立的帳號
2. 進入「設定」→「Messaging API」
3. 點擊「啟用 Messaging API」
4. 選擇或建立一個 Provider（例如：`Voice-Notion`）

**步驟 C：獲取 Channel Access Token**
1. 前往 [LINE Developers Console](https://developers.line.biz/console/)
2. 點擊進入剛才在 OA Manager 建立的 Channel
3. 切換至「Messaging API settings」分頁
4. 滑到底部「Channel access token」區塊，點擊「Issue」
5. 複製產生的長字串 Token

#### 官方文件
- **快速開始**: https://developers.line.biz/en/docs/messaging-api/getting-started/
- **Channel Access Token**: https://developers.line.biz/en/docs/messaging-api/channel-access-tokens/
- **Push Message API**: https://developers.line.biz/en/reference/messaging-api/#send-push-message

#### 配置範例
```bash
LINE_CHANNEL_ACCESS_TOKEN=abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGHIJKLMNOPQRSTUVWX
```

---

### 4. LINE_USER_ID

**用途**：指定接收 Line 推播通知的目標使用者 ID。

#### 取得步驟

**方法一：透過 LINE Developers Console (推薦)**
1. 登入 [LINE Developers Console](https://developers.line.biz/console/)
2. 進入對應的 Provider 與 Channel
3. 在「Basic settings」分頁最下方，可以找到你的「Your user ID」 (格式為 `U` 開頭的 32 位字串)

**方法二：透過 Webhook 事件**
1. 在 Channel 設定中啟用 Webhook 並設定伺服器
2. 使用手機加入 Bot 為好友並傳送訊息
3. 從 Webhook 的 `event.source.userId` 取得 ID

#### 官方文件
- **取得使用者 ID**: https://developers.line.biz/en/docs/messaging-api/getting-user-ids/
- **Webhook 事件**: https://developers.line.biz/en/reference/messaging-api/#webhook-event-objects

#### 配置範例
```bash
LINE_USER_ID=Uabcdef1234567890abcdef1234567890
```

#### 常見問題
- **Q: 如何快速取得自己的 User ID？**
  - A: 可暫時在後端 `/webhook` 端點加入 log，傳送訊息給 Bot 後從 log 中取得

---

## ⚙️ 選用環境變數

### REDIS_URL
**預設值**: `redis://redis:6379/0`

**說明**: Celery 使用的 Redis Broker 連線 URL。若使用 Docker Compose，保持預設值即可。

**參考文件**: https://redis.io/docs/getting-started/

---

### NOTION_DATABASE_ID
**預設值**: `""` (空字串)

**說明**: 若有預設的 Notion 資料庫，可填入其 ID。留空時，系統會使用 Smart Routing 自動判斷目標頁面。

**取得方式**: 開啟 Notion 資料庫頁面，URL 中 `notion.so/` 後的 32 位英數字串即為 Database ID。

**範例**: 
```
https://www.notion.so/myworkspace/a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
                                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                         Database ID
```

---

### APP_NAME
**預設值**: `Voice-Notion`

**說明**: 應用程式名稱，用於日誌與錯誤訊息。

---

### DEBUG
**預設值**: `false`

**說明**: 設為 `true` 啟用詳細除錯日誌。生產環境請保持 `false`。

---

## ✅ 驗證配置

建立 `.env` 檔案後，可透過以下方式驗證配置：

```bash
# 啟動服務
docker-compose up -d

# 查看 Web 容器日誌
docker-compose logs web

# 查看 Worker 容器日誌
docker-compose logs worker
```

若配置正確，應看到服務正常啟動，無 API Key 或 Token 相關錯誤訊息。

---

## 🔒 安全提醒

- ✅ 確保 `.env` 已加入 `.gitignore`，避免提交至版本控制
- ✅ 定期輪換 API Key 與 Token
- ✅ 生產環境請使用環境變數或密鑰管理服務（如 AWS Secrets Manager、Google Secret Manager）
- ❌ 切勿在程式碼或文件中硬編碼真實的 API Key

---

## 📚 相關文件

- [專案 README](../README.md)
- [專案需求書](./PROJECT_REQUIREMENTS.md)
- [Gemini API 範例](./GEMINI_EXAMPLE.py)

---

## 🆘 遇到問題？

若在設定過程中遇到問題，請檢查：
1. 環境變數名稱是否拼寫正確（區分大小寫）
2. Token 是否已過期或被撤銷
3. Notion Integration 是否已授權目標頁面
4. Line Bot 是否已加為好友

更多問題請參考各服務的官方文件或提交 Issue。
