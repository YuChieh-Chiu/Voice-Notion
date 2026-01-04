"""
Routing Prompt for LLM Stage 1
LLM 第一階段：路由判斷
"""

ROUTING_PROMPT = """你是一個專業的筆記助理。請分析以下語音逐字稿，並判斷：

1. **操作類型 (action)**：
    - `create`：建立新頁面（全新主題或與現有頁面無關）
    - `append`：追加到現有頁面（內容與目標頁面主題高度相關，可被一同紀錄）

2. **筆記標題 (title)**：簡潔扼要描述本次筆記內容

3. **摘要模板 (template_type)**：
    - `meeting`：會議紀錄（包含參與者、決議事項）
    - `idea`：靈感記錄（創意發想、想法）
    - `todo`：待辦事項（任務清單）
    - `general`：通用筆記（無特定類型）

4. **目標頁面 (target_page_id)**：選擇最適合的 Notion 頁面

---

**逐字稿**：
{transcript}

**可用的 Notion 頁面**：
{pages}

---

請根據以上資訊進行判斷。
"""
