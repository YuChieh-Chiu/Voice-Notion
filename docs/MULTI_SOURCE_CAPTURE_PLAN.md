# Voice-Notion 多來源收件實作計畫

2026-10-21 定稿；分支 `feature/multi-source-note-capture`。
本文件取代早期方案，產品決策依使用者最後確認；討論沿革見 [互動文件](CAPTURE_DESIGN_DISCUSSION.html)。

## 已確認的第一版範圍

- 一個自行部署實例服務一位主要使用者。Siri／HTTP 與 LINE 共用內容處理、狀態與主要目的地。
- 目的地可選 Google Drive 或 Notion，不預設雙寫，不搬移舊 Notion。
- Drive 目標可直接指定 `Atlas/raw` 的 folder ID；不另建 Atlas 保存模組，不修改 Atlas 規則、索引或 wiki。啟用前確認目標授權及目錄約定。
- 文字保存 `original.txt` 與 `note.md`；語音保存原音檔、`transcript.txt` 與 `note.md`。來源資訊併入 `note.md`，不另存 `source.json` 或潤飾稿。
- **圖片與 PDF／一般檔案第一版只保存收到的原檔，不執行 OCR、全文擷取或附件摘要。** 不執行附件中的程式。
- Notion 只保存完整文字／逐字稿、摘要與來源資訊，不上傳附件。音訊只供 STT 暫存，可靠寫入並處理完成後清理；Notion 模式不接受圖片／PDF 原檔，也不自動改存 Drive。
- 分類可停用或自訂，空白 Drive 從第一天即可分類。個人偏好為 Jev，主題為 finance（投資理財）、fitness（運動）、tech（AI 技術知識學習）。fork 可自行配置；模型只回傳允許的代號，未知或故障進待分類。
- 使用 LINE 指令與按鈕管理；原始附件需先確認完整原文與摘要，才走「只清原始附件」捷徑。**第一版只移至垃圾桶，不永久刪除、不清空帳號垃圾桶、不按天自動清理。**

## 收件與處理

1. 驗證入口身分、大小及格式；建立穩定來源 ID 和不含原文的佇列任務。
2. 實際收到的文字／位元組先保存到私人、持久化暫存；記錄管道、時間、原始名稱、作者未確認與來源 URL（未提供則空）。LINE 依訊息 ID 去重。
3. Drive 先保存原始來源，再做 STT／摘要。圖片與檔案保存原檔即完成。失敗不假造全文或摘要。
4. 核心與保存模組分離；既有 Siri／BYOK 回應相容。BYOK 僅寫自己的 Notion，不得存取部署者 Drive 或收件管理。
5. 分階段紀錄原檔、轉錄、摘要、筆記、通知進度；重試重用成果，雲端用穩定 ID 去重，通知失敗不重建來源。
6. 雲端成功不代表 Mac 已同步。原檔成功、摘要失敗與全部完成的通知須區分。

## 保存與分類

Drive 使用使用者 OAuth。依 2026-10-23 後續討論，新部署改用 Web OAuth，本機與 Linode 共用固定 `/oauth/google/callback` 流程；client secret／Fernet key 留在私人設定，refresh token 與根目錄加密存於 web／worker 共用 SQLite。部署者驗證、限時一次性 state、瀏覽器綁定、PKCE、同帳號／client／根目錄約束為必要防護。舊環境變數與 Desktop 工具僅保留相容，不自動遷移。僅操作官方固定端點，不接受模型指定 URL 或路徑。分類結果經允許清單映射。

目錄：`<目標>/<主題（可省略）>/<年月>/<來源 ID>/`。停用分類按日期保存；未知分類進待分類。原檔先落地，分類可決定或調整該筆資料夾位置。

Notion 頁面樹與 create／append 留在 Notion 模組；每筆有可定位來源區塊及完整原文，不以摘要冒充完整來源。

持久化管理紀錄保存來源／檔案 ID、狀態、校對及刪除標記；不把全文複製到日誌或 broker。SQLite 適用本版同機 web／worker 共享 volume 的個人部署；Redis result backend 不當永久來源紀錄。

## LINE 管理合約

| 指令 | 行為 |
| --- | --- |
| `/help`、已送出的 `/` | 列出可用指令與簡短說明 |
| `/files C123`、`/file C123` | 列出該筆與檔案代號、狀態、私人連結；兩者等價 |
| `/review C123` | 標記完整原文與摘要已校對，不清理 |
| `/clear C123 original` | 預覽只移除原始附件，保留文字 |
| `/clear file F456 [F789…]` | 預覽移除指定單檔或跨筆檔案清單 |
| `/clear C123 all` | 預覽移除整筆服務管理內容 |
| `/clear reviewed` | 預覽批次移除已校對的原始附件，可依主題與日期縮小 |
| `/confirm 代號`、`/cancel 代號` | 以預覽提供的同一限時代號確認或取消，供桌面操作 |

代號為示例。省略範圍時詢問，不猜測；指令與通知按鈕共用服務，不由 LLM 決定刪除。

回覆依序呈現 Note-Goblin 事件名稱、紀錄 ID、狀態、適用的內容／檔案／連結、下一步與必要提醒。C 開頭為紀錄 ID，F 開頭為檔案 ID；不把背景工作 UUID 當成紀錄 ID。來源標題明確標為標題，不冒充摘要；不為通知額外查雲端或呼叫模型。圖片／PDF／一般附件只回報保存原檔，Notion 不提示 Drive 同步，BYOK 不顯示部署者管理指令。

Bot 只能處理送達的訊息，無法在 LINE 原生輸入框尚未送出 `/` 時顯示自訂自動完成。已送出的 `/` 可回覆說明；適用操作沿用 Quick reply（iOS／Android，可能隨新訊息消失），桌面保留文字指令。未建立 Rich menu 或獨立 LIFF 輸入介面。[LINE 收件事件](https://developers.line.biz/en/docs/messaging-api/receiving-messages/)、[Quick reply](https://developers.line.biz/en/docs/messaging-api/using-quick-reply/)

流程：列明確清單、總大小與影響 → 確認／取消 → 僅處理該清單 → 逐檔回報。確認限時、綁定使用者與清單；過期或資料變更重新預覽；重送確認不重複執行。垃圾桶仍占容量，永久刪除由使用者到 Drive 操作。

LINE 預覽完整文字超過 4400 字或超過 45 檔時先取消該預覽，需縮小範圍；45 檔上限用來保留最壞部分失敗的完整回覆。從 HTTP 建立的較大清單拿到 LINE 確認，本次 LINE 呼叫不執行或續作清理，也不變更原清單，改由管理 API 完成操作或查看完整結果；該批可能已在 HTTP 執行，不宣稱整批尚未清理。HTTP 仍沿用設定的批次上限。

原檔捷徑排除未校對、失敗與只有原檔的收件。指定檔案／整筆仍可明確移除唯一內容。僅處理本服務登記檔案，不得因位於 Atlas/raw 就刪除其他來源。

清原音時，保留的逐字稿／筆記須保持本機狀態與 revision、遠端 ID／位置／大小／MD5／私人權限及實際 SHA256 一致。Drive 的 version 也可能因不可見 metadata 改變，僅此欄位不同且內容雜湊一致可繼續；每次確認及鎖內執行都核對實際保留內容。待刪檔案仍使用包含 version 的完整預覽快照驗證。

刪除意圖先持久化並停止工作，防止重送建回；單檔只阻止對應產物再生。更新仍存在筆記的來源狀態與連結。整筆清理暫存與已知重試副本，保留最少去重標記。LINE unsend 走整筆撤下，不要求再點確認；不承諾清除 Atlas wiki、另存副本或供應商備份。

## 個人部署防護

- LINE 原始 body 驗證簽章，僅接受設定 user ID 私聊；群組不收件。收回及按鈕同樣驗證。
- Siri 金鑰不可為空，比較採常數時間；管理 API 只供部署者，BYOK 不得取得個人資料。
- 收件大小、下載及模型逾時設上限；僅用 LINE 官方下載端點，不抓原文內任意網址。
- 原文、逐字稿、token、模型金鑰不進一般日誌或例外回覆；必要的 BYOK context 加密。Docker build 排除憑證及資料。
- STT 本機執行；Gemini 摘要及 Jev 分類會收到相關文字。圖片／PDF 不送模型。停用的目的地不要求憑證。

## 分階段實作與提交

| 階段 | 交付 | 驗證重點 |
| --- | --- | --- |
| 0 | 決策文件、實作紀錄定稿 | 最後確認與文件一致 |
| A | 設定、來源模型、持久化與個人授權 | 去重、路徑、空金鑰、BYOK 隔離 |
| B | 共用處理、Notion 純文字保存 | 完整原文、相容入口、重試、不存附件 |
| C | Drive OAuth、保存、Jev 分類 | 原檔一致、檔案數、未知分類、續傳、私人範圍 |
| D | LINE 收件、下載、通知與校對 | 簽章、白名單、私聊、大小、事件重送 |
| E | 清理 API、LINE 指令／按鈕、收回 | 確認、過期／重放、單檔／批次、競態 |
| F | 整合回歸與部署說明 | 離線全流程、憑證與 volume 配置 |

每階段完成後：測試／lint → **獨立 subagent 審查計畫符合度、clean code 與 Medium／High／Critical 資安風險** → 修正必修項、必要時再審 → commit → push 同一 feature 分支。不直接推 main、不 force push。超過十檔時按可驗證子任務分批提交。

2026-10-23 的 Web OAuth 擴充不代表整站已適合公開部署：一般 BYOK／轉送 IP 限流、未授權 multipart 暫存及舊 Demo CDN 風險仍須另行修正；Linode 的目標平台映像、HTTPS／代理日誌、資源與持久化必須在正式環境另驗。

參考指定 commit／code-review 技能的通用檢查；Wayken 專屬的多品牌、唯讀資料層、skills 鏡像不是本 repo 規則。測試用替身，不打真實 Drive／LINE／Notion 或模型；雲端權限與實帳號驗證在配置後另做。

實作紀錄見 [Implementation notes](implementation_notes/impl_notes_feature_multi-source-note-capture_20261021.html)。

## 官方依據

- [Drive 上傳](https://developers.google.com/workspace/drive/api/guides/manage-uploads)、[OAuth 權限](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)、[垃圾桶](https://developers.google.com/workspace/drive/api/guides/delete)
- [LINE 簽章](https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/)、[事件與收回](https://developers.line.biz/en/docs/messaging-api/receiving-messages/)、[按鈕](https://developers.line.biz/en/docs/messaging-api/actions/)
- [TypeSafe／Jev 題型](https://docs.typesafe.ai/primitives)
