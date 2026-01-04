# Prompt Template Guide

Voice-Notion 使用 **模板驅動 (Template-Driven)** 的摘要生成機制。所有模板位於 `backend/app/prompts/templates/` 目錄下。

## 1. 模板總覽

系統目前內建四種標準模板：

| 模板檔案 | 用途 | 觸發條件 |
|:--- |:--- |:--- |
| `meeting.md` | **會議紀錄** | 當錄音內容涉及多人討論、決策、專案進度時 |
| `idea.md` | **靈感/想法** | 當錄音內容為個人隨想、創意發想、腦力激盪時 |
| `todo.md` | **待辦事項** | 當錄音內容包含明確的任務、清單、購物列表時 |
| `general.md` | **通用筆記** | 當內容不屬於以上三類，或性質模糊時 |

## 2. 模板結構與變數

所有模板皆使用 **Liquid-style** 變數（但在 Python 程式碼中是直接替換或由 LLM 生成）。
目前的實作機制是將模板內容作為 System Prompt 的一部分，指示 LLM 依照該格式輸出。

### 通用規範 (`_spec.md`)
所有模板都隱含繼承了通用規範，包括：
- 使用**繁體中文 (Traditional Chinese)**
- 保持結構清晰 (Markdown)
- 修正語音轉錄錯誤 (Typos)

### 模板範例 (Meeting)
```markdown
# 📅 會議紀錄：{{ title }}

## 參與者
- [LLM 根據內容推斷]

## 📝 重點摘要
- 重點 1...
- 重點 2...

## ✅ 決議與待辦
- [ ] 任務 A
- [ ] 任務 B
```

## 3. 如何新增/修改模板

若需調整 AI 生成的格式，請直接編輯對應的 `.md` 檔案。

### 步驟
1. 開啟 `backend/app/prompts/templates/`。
2. 編輯目標 `.md` 檔案 (例如 `meeting.md`)。
3. **無需重啟 Server**，LLM Service 會在每次請求時動態讀取最新內容。

### 注意事項
- 保持 Markdown 語法正確。
- 盡量提供明確的 `Headings` (#, ##) 指引 LLM 分段。
- 若需新增全新的模板類型 (例如 `diary.md`)，需同步修改：
    1. `backend/app/prompts/routing.py` (讓 Routing Stage 知道有新類型)
    2. `backend/app/services/llm_service.py` (確保程式碼能載入)
