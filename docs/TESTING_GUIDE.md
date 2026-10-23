# 逐項測試與驗收指南

2026-10-23；包含 `964b1a1` 收件基準與後續 Web OAuth 擴充。本指南對應 [已確認計畫](MULTI_SOURCE_CAPTURE_PLAN.md)，從 Google Console 到授權的完整步驟見 [部署指南](DEPLOYMENT_GUIDE_ADMIN.md#drive-web-oauth從-google-console-到可測試)。

**建議順序：離線回歸 → 隔離環境 → Drive 收件與分類 → LINE 清理／收回 → 故障恢復 → Notion／BYOK 相容。** 先走下方「第一次驗收」，再逐項勾選表格。

`/health` 正常只代表 HTTP 服務可用，`202` 只代表已保存收件並嘗試排入工作；都不能單獨當成整條流程通過。135 項離線測試使用外部服務替身，不等於已驗證真實 OAuth、STT 品質、模型摘要、LINE 帳號或 Mac 同步。

## 1. 先跑離線回歸

在 repo 的 `backend` 目錄執行，建議使用與映像一致的 Python 3.13：

```bash
poetry install --only main,dev,setup
PYTHONPATH=. poetry run pytest -q
poetry run ruff check app scripts/drive_oauth.py tests
poetry check --lock
```

此功能基準應有 **135 passed**；未來新增測試以實際輸出為準。測試不解析私人 `.env`，使用假憑證與暫存目錄，不需要 Redis、真實雲端或下載 Whisper 模型。套件安裝本身需要網路。

| 功能 | 對應測試（位於 `backend/tests/`） | 能證明的範圍 |
| --- | --- | --- |
| 身分、輸入與 Siri 相容 | `test_dependencies.py`、`test_capture_routes.py` | 空／錯金鑰、BYOK 管理隔離、回應相容、串流大小與來源保留 |
| 持久化與去重 | `test_capture_repository.py` | 同一來源、不同 owner、重新開啟 DB、不可重生與安全路徑 |
| Notion 全文、摘要與重試 | `test_capture_processor.py`、`test_notion_service.py` | 原文完整分塊、頁面操作、寫入不確定時保守停止、通知不重跑模型 |
| Drive 精簡檔案與範圍 | `test_drive_storage.py` | 2／3／1 檔、原始位元組、固定 ID、私人根目錄與移出範圍拒絕 |
| 真實 SDK 續傳流程 | `test_drive_resumption.py` | 離線 transport 模擬首塊／最後回應遺失、server Range、404／410 |
| 分類、摘要與 STT 介面 | `test_topic_router.py`、`test_llm_service.py`、`test_stt_service.py` | 主題允許清單、供應商失敗、模型參數；不證明實際分類或轉錄正確率 |
| LINE 收件與通知 | `test_line_ingestion.py` | 原始簽章、私聊白名單、有限下載、重送與推播去重 |
| 清理與收回 | `test_deletion_service.py` | 校對、確認、取消／過期、部分失敗、保留全文 guard、刪除競態與 Notion 區塊範圍 |
| 串接、跨筆批次與隱私 | `test_capture_integration.py` | 路由／DB／處理器串接、跨 owner 拒絕、task 回傳不含原文、Notion 外加子內容保護 |
| OAuth 初始化工具 | `test_drive_oauth.py` | 官方端點與 desktop client、拒絕混合設定、0600 新檔、不覆寫既有檔 |
| Web OAuth／共用憑證 | `test_drive_web_oauth.py` | owner 前置驗證、瀏覽器綁定、PKCE、過期／取消／重放、加密、worker 讀取、同帳號／root、pending 恢復與重新授權 |
| 連線頁／Demo 安全資產 | `test_web_portal.py` | 本地資產、CSP／no-store、原始 HTML 不繞過安全標頭、共享樣式與 script nonce |

只重跑某組：`PYTHONPATH=. poetry run pytest tests/test_deletion_service.py -q`。

## 2. 建立獨立的真實驗收環境

這一層由操作者填入測試憑證後執行，會呼叫模型與雲端服務、使用帳號額度。本次文件補充不會替你啟動服務或送出測試內容。

使用 repo 根目錄的 [docker-compose.uat.yml](../docker-compose.uat.yml)：

| 項目 | 驗收環境 |
| --- | --- |
| 設定檔 | `.env.uat`，不讀 Compose 的正式 `.env` |
| API／Swagger | `http://localhost:8081`、`http://localhost:8081/docs` |
| 暫存與 SQLite | `./data-uat:/data`，與正式 `./data` 分開 |
| Redis | UAT project 的私人 Redis／named volume；覆寫外部 `REDIS_URL` |
| Drive | 新建專用 `Voice-Notion-UAT` 目錄，不使用正式 Atlas/raw |
| LINE | 專用測試 Channel，避免更改正式 Channel 的 webhook |
| Notion | 專用測試根頁與 integration 授權範圍 |

在 repo 根目錄執行一次：

```bash
# 若已有 .env.uat，保留它；不要覆寫既有設定
test -e .env.uat || cp .env.example .env.uat
chmod 600 .env.uat
mkdir -p data-uat
chmod 700 data-uat

# 以下函式供同一個終端機後續使用；不要漏掉 -p 或 --env-file
vn_uat() {
  docker compose -p voice-notion-uat --env-file .env.uat -f docker-compose.uat.yml "$@"
}
```

用私人編輯器填妥 `.env.uat`：不同於正式服務的 `SIRI_API_KEY`、固定共用 `TASK_ENCRYPTION_KEY`、Web Drive client ID／secret／`DRIVE_OAUTH_REDIRECT_URI`。新連接的 `DRIVE_FOLDER_ID`／`DRIVE_REFRESH_TOKEN` 留空，透過 `http://localhost:8081/oauth/google/setup` 建立 `Voice-Notion-UAT` 私人根目錄。依部署指南在同一瀏覽器完成授權，owner status 必須 `connected=true`。token 加密保存於 UAT SQLite，worker 可直接讀取；不用抄寫 token。摘要另需 `GEMINI_API_KEY` 及可用額度；LINE 純推播只需 access token＋user ID，收件／指令／按鈕／收回才另需 Channel secret＋公開 HTTPS webhook。缺少部分只標為未驗收。

初次設定建議先固定標記分類，避免把模型判斷與管線問題混在一起：

```dotenv
CAPTURE_DESTINATION=drive
CAPTURE_CLASSIFICATION=custom
CAPTURE_ROUTER=rules
CAPTURE_CATEGORIES='[{"id":"finance","description":"投資理財"},{"id":"fitness","description":"運動"},{"id":"tech","description":"AI 技術知識學習"}]'
```

完成後：

```bash
vn_uat up -d --build
vn_uat ps
curl --fail http://localhost:8081/health
```

四個服務應啟動；health 回 `{"status":"healthy"}`。第一次語音轉錄會下載 STT 模型，記錄暖機與後續處理時間，不預設固定完成秒數。

Web OAuth 先驗：不帶管理 key 不能 start／finish／status；錯誤或重放 callback 不改連接；Google 同意後建立私人根目錄、API status 不暴露 token；重啟服務仍連接。資料夾建立失敗按 finish 重試，同一 root ID 不增加副本；pending token 到期可重新同帳號授權。若未實際故障注入，恢復分支只記離線通過。Linode 外層代理另核對 callback query 不進日誌。本機映像若為 arm64，不能當成 Linode amd64／原生 STT 已驗收。

2026-10-23 本機真實 UAT 紀錄：操作者已完成 Web Google 同意，24 項檢查通過，覆蓋共用加密連接、以儲存的 refresh token 換取 access token、真實 HTTP／worker 原檔上傳與 SHA256 讀回比對、明確主題、重試同一 cloud ID 且不增副本、預覽／取消／確認真實垃圾桶、刪後拒絕重建、正常重啟後授權及 tombstone 保留、SQLite／WAL 與當次應用／代理日誌的秘密檢查。測試只建立並清理專用 UAT 的一筆來源，不永久刪除；私人證據在 `data-uat/drive-live-20261023.json`，不提交 Git。

上述第一輪故意保持 Gemini key 空白，驗證原檔成功後的摘要失敗狀態。沒有等待 access token 自然過期，也沒有真實撤銷 refresh token；不要將離線故障測試改列為真實通過。

同日補齊部分供應商設定後的第二輪紀錄如下；這是各項證據的範圍，尚未完成全表驗收：

| 功能 | 真實驗證結果 | 證據與限制 |
| --- | --- | --- |
| 圖片／PDF／一般附件原檔與清理 | 25 項通過 | 直接呼叫正式 CaptureProcessor，寫入真實 Drive，再走 HTTP 管理 API；4 筆新造的 PNG／PDF／ZIP／文字附件各只保存原檔，下載位元組及 SHA 一致，涵蓋明確主題、未分類、停用分類、單檔預覽／取消／確認、跨筆檔案去重批次垃圾桶、整筆 tombstone 與重試拒絕。只清理這 4 筆測試原檔；未走 LINE 收件或一般附件 HTTP 上傳入口。 |
| 文字／語音完整保存與內容讀回 | 5 筆流程完成，40 項讀回檢查通過 | HTTP 三主題文字各 2 檔；Siri binary API 與 multipart API 各一筆合成語音、各 3 檔，語音經 Jev 分為 fitness。雲端原檔與送出位元組完全一致，逐字稿及摘要 SHA、來源 metadata／連結、私人權限與確切檔案角色通過；不是 iPhone 捷徑或 LINE 手機收件測試。 |
| Jev 語意分類 | 9／9 簡單案例符合預期 | 三主題各 3 句、不帶 # 標記，呼叫真實 Jev；不是廣泛準確率評估，也不是完整文字摘要管線通過。 |
| Whisper 本機 STT | 一段合成語音的 5 項語意檢查通過 | 11.49 秒中文語音，small／CPU／int8；活動、星期、時間、時長及「尚未完成」皆保留。模型已下載至 UAT 私人快取；本次約 5.1 秒包含快取後模型載入，非效能保證、真實手機語音或完整三檔流程。 |
| LINE 發送通知 | API 接受，操作者確認手機收到測試推播 | 這只證明已發出的指定測試推播送達，未逐則人工確認其他流程通知。`notified` 僅是應用狀態，通知停用時也可能為 true，不能單獨證明 API 接受或手機送達。 |
| Gemini 摘要與額度故障恢復 | 額度恢復後 5 筆完成；內容品質仍需人工校對 | 初始生成診斷回覆 402 RESOURCE_EXHAUSTED／預付額度耗盡，worker status 只記 RuntimeError。操作者更新額度後重試同一 CID 成功，原檔 cloud ID 保持不變、真實目錄恰為 2 檔，另兩主題與兩筆語音亦完成。五筆保留核心金額、時間及否定語意，但有下述品質限制，不代表完整忠實或零幻覺。 |
| 校對後批次清語音原檔 | 19 項檢查通過，有客戶端等待限制 | 經 HTTP 管理 API 驗證未校對拒絕、校對更新 note、單筆預覽取消及取消 token 拒絕、依 fitness／日期精確選兩筆音檔。批次確認客戶端等候 40 秒後逾時；只讀核對同一預覽的伺服器紀錄為 done／complete，再讀回確認兩音檔在真實垃圾桶、逐字稿未改、摘要與移除註記正確、舊連結撤下。未重做刪除；完成後重試不建回原檔，每筆仍保留文字兩檔。 |
| 還原設定後的狀態與入口防護 | 15 項檢查通過 | web／worker 重建後授權、五筆完成狀態、兩筆未校對與兩音檔垃圾桶狀態保留；未帶管理 key 的管理／OAuth status 為 401。實際私人設定值未出現於當前容器日誌；此掃描不含已刪除容器的日誌，不等同完整日誌或整站資安稽核。 |
| LINE 收件／指令／按鈕／收回 | 第二輪依使用者要求跳過 | 當時缺 Channel secret；第三輪已補入並接續下方測試。 |
| Notion／BYOK、Linode、Mac 同步 | 未驗收 | 未配置專用 Notion 測試權限；本機 UAT 不能替代正式主機與手機端驗收。 |

**摘要品質限制：** 測試操作者與 review_stage_e 獨立核對發現，finance 多加原文沒有的「會議記錄」背景，tech 將「尚未完成實作」強化為「尚未進入程式碼實作」。一般模板允許的補充建議也不是原文。這兩筆維持未校對，不因模型成功就批准原檔清理；只有兩筆已核對一致的語音用於 review／清理驗收。這輪只驗特定測試案例，不能當成模型準確率保證。

第二輪私人證據：`data-uat/full-uat-{attachments,jev,stt,line-push,core,content,reviewed-cleanup,final}-20261023.json`，不提交 Git；附件等直接 harness 測試不能取代對應入口的真實事件。API 金鑰與 OAuth 憑證未寫入公開報告。暫時改成 Jev 的 UAT Router 於測試後還原為原本的 rules，只恢復該設定，不覆蓋操作者補入的憑證。批次確認若等待逾時，先查實際狀態並保留原確認 token，不要據此當作失敗而另開新批次。

查找 LINE Channel secret：登入 [LINE Developers Console](https://developers.line.biz/console/)，選 Provider → 這個 **Messaging API Channel** → **Basic settings** → **Channel secret**，需要該 Channel 的 Admin 權限。複製既有值到私人 `.env.uat` 的 `LINE_CHANNEL_SECRET`；不要貼到聊天。單純查找不需按 **Issue**，重新簽發會讓舊 secret 失效。[LINE 官方說明](https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/#get-channel-secret)

第三輪補測（2026-10-23）：操作者補入 Channel secret 後，重新載入獨立 UAT，先區分本機合成事件與 LINE 平台事件，不混合宣稱：

| 範圍 | 已有證據 | 驗收界線 |
| --- | --- | --- |
| 本機簽署合成 webhook | 16 項通過 | 使用真 secret，經 nginx／web／Redis／worker／Drive／Gemini；驗未簽章、錯簽、原始 body 變動拒絕，空事件、格式／大小、白名單／群聊、加密待處理事件、同 event 重送與兩檔保存。另一個相同 message ID 的新 event 僅確認 HTTP 接受；未以此單獨證明該新 event 處理完成後去重。 |
| 本機合成指令／postback／unsend | 14 項通過 | 雲端原文讀回、來源連結、指定 /files、核對摘要後 review、文字原檔捷徑拒絕、只選 note 的清理預覽／取消／取消後拒絕確認／新確認、收回後整筆 tombstone／垃圾桶與防重放。只操作本輪唯一合成 CID／FIDs，不使用寬泛批次。 |
| 真實 LINE 平台與暫時入口 | 7 項通過 | LINE 官方 webhook test 向指定 HTTPS 入口發送真簽章空事件，得到 200；同時確認未簽章拒絕及 Demo／OAuth／Swagger／管理／health 均不公開。空事件驗證不代表手機訊息、附件或實際按鈕已通過。 |
| 手機文字／語音／圖片／PDF／一般檔案、點擊按鈕及收回 | 等待實際操作 | 需在 Console 設定測試 webhook URL 並開啟 Use webhook，再私聊傳訊、點擊及收回。附件須以 LINE 平台實際 message ID 下載比對；圖片可能經 LINE 處理，不能假定與相簿原檔相同。 |

暫時入口依操作者選擇建立，只轉發本機 8091 gateway；gateway 只允許正確 HMAC 的 `POST /api/v1/line/webhook`，再以固定 Host localhost 轉送 8081，不公開整個 UAT。gateway 限 1 MiB、最多 8 個連線及 45 分鐘期限，關閉請求 body 日誌及本機 inspector，只將事件 ID／類型等 metadata 寫入私人報告；這是暫時測試入口，不是完整 DoS 防護。手機測試需要本人操作 LINE 與 Console，不能以程式合成事件代替。測試後關閉入口，並停用或清除臨時 Channel webhook，保留原設定紀錄。到期清理若 GET 檢查時已是其他網址則保留；LINE API 沒有原子條件更新，請勿在清理期間同時更改 URL。

第三輪私人證據：`data-uat/line-{local-uat,command-uat,platform-uat,gateway}-20261023.json`；均不提交 Git。review_stage_f 已只讀獨立核對 16／14／7 項證據與 fixture 清理範圍，並指出同 message 新 event 的處理時序限制；手機項目仍保留未驗收。

第四輪真實 LINE 私聊補驗（2026-10-23）：操作者開啟 Use webhook，傳入一筆文字及 PDF、TXT、JPG；平台未提供傳送端裝置類型，故只稱真實 LINE 私聊，不推論已測某個手機版本。四筆均完成，17 項雲端讀回檢查通過：文字兩檔，三個附件各只有一份原檔；附件以本輪真實 message ID 再向 LINE 內容 API 下載，與 Drive 原檔 SHA256 完全相同，且檔案權限為私人。附件按鈕缺少先以官方照片／影片接收設定排查，後續已觀察到 image／file 事件及保存成功；官方帳號自動回應與保存通知須分別設定。語音、實際點擊管理按鈕及真人收回仍待另驗。

另對這筆已保存的真實文字，以**本機合成的新 event** 重送相同 message ID；明確等待該 event worker complete，再核對同 CID、FID、cloud ID、SHA 均不變，目錄仍恰為兩檔，4 項去重檢查通過。這補足第三輪時序缺口，仍不宣稱已觀察 LINE 平台真正 redelivery。此輪不替使用者標記 review，也未刪除傳入附件。詳細私人證據在 `data-uat/line-phone-uat-<session>.json`，不公開原文、實際 ID 或臨時 URL。

臨時入口原本到期清除 URL 的請求實際收到 LINE 400，未假報已還原；本輪重開改為只在期限到時關閉精確同一 tunnel，Channel URL／Use webhook 仍由操作者在 Console 清理。應確認程序確實退出，不能將「已要求停止」當成退出證明。

正式反向代理若直接接 UAT 的 8081，需把公開 hostname 加到 `.env.uat` 的 `ALLOWED_HOSTS`（保留 `localhost,127.0.0.1`）；這與本輪只公開 8091 gateway 的暫時方案不同，不要將整個 8081 經臨時 tunnel 公開。測試 Channel webhook 使用 `https://測試網域/api/v1/line/webhook`，在 Console 啟用 Use webhook／redelivery。驗證 webhook URL 成功仍不代表內容下載、白名單或推播已通過；這些要另外測。純推播不需要公開入口或 Channel secret。[LINE 官方收件與驗簽說明](https://developers.line.biz/en/docs/messaging-api/receiving-messages/)

**每次修改 `.env.uat` 都要重建容器設定；只用 restart 不會載入新環境變數：**

```bash
vn_uat up -d --force-recreate web worker
```

## 3. 第一次驗收：先完成一筆，再做清理

1. 打開 `http://localhost:8081/docs`，展開 `POST /api/v1/captures/text`，按 **Try it out**，填測試的 `X-API-Key`。送出下列 JSON：

   ```json
   {
     "text": "#finance UAT-T01\n投資預算為1000元。\n還沒有購買任何ETF，下一步只比較費用。",
     "source_url": "https://example.org/uat-source"
   }
   ```

2. 收到 `202` 後記下真正的 `capture_id`（`C` 開頭）與 `task_id`；本指南的 `C…`／`F…` 都要換成實際代號。
3. 在 Swagger 的 `GET /api/v1/captures/{capture_id}` 填相同金鑰查詢。等 `capture.status=complete`，兩個 `files[].state=uploaded`。
4. 到 Drive 找 `<UAT根目錄>/finance/<UTC年月>/<C代號>/`，應只有 `original.txt`、`note.md`。前者與輸入原文一致；後者有摘要、來源 ID、作者未確認與你提供的來源 URL。網址只是 metadata，不抓網頁內容。
5. LINE 應收到「完整原文與摘要已保存」，附同一個 C 代號與私人連結。確認摘要沒有把「還沒買」改成「已買」。
6. LINE 輸入 `/files C代號`。先用這筆文字測 `/clear C代號 all`，檢查預覽後按 **取消**，Drive 內容應仍存在。
7. 再送一段短語音，確認有原音檔、完整逐字稿與摘要三檔。人工作完校對後輸入 `/review C語音代號` → `/clear C語音代號 original` → 確認。應只把音檔移至垃圾桶，留下兩個文字檔。

完成這七步代表你已走過「收件 → 保存 → 通知 → 查詢 → 取消 → 校對 → 確認清原檔」。**還沒有驗收下面的故障、去重、權限與 Notion 相容行為。**

## 4. Drive 與各入口逐項驗收

每筆使用不同的 `UAT-…` 標記；記錄 C／F 代號。原檔比較請下載 Drive 檔案後執行 `shasum -a 256 本機測試檔 下載檔`，兩個雜湊值應相同。LINE 圖片的比對基準是服務從 LINE 內容 API 實際取得的資料，不能直接假設等於手機相簿檔案；另外把同一張圖以「檔案」方式傳送，作本機檔案比對測試。

| 勾選／編號 | 操作 | 通過條件與證據 |
| --- | --- | --- |
| [ ] D01 HTTP 文字 | 做前節 UAT-T01 | 202 有 CID；最後 complete；2 檔；原文與換行保留，摘要沒有反轉否定句 |
| [ ] D02 LINE 文字 | 私聊傳 `#tech UAT-T02` 加兩段文字 | 2 檔且分類 tech；LINE 通知／`/files` 的 CID 與 API、Drive 一致 |
| [ ] D03 HTTP multipart 音訊 | Swagger `POST /api/v1/note` 上傳小型 WAV／M4A | 202 回應相容；3 檔；原音檔 SHA256 相同、原始檔名 metadata 保留 |
| [ ] D04 Siri binary 入口 | `POST /api/v1/note/ios` 傳同一音檔位元組 | 202，最後 3 檔；完整轉錄與摘要。要驗收手機捷徑，使用 UAT 的公開 HTTPS 入口（不是手機上的 localhost），在 iPhone 真正執行一次並記錄 CID |
| [ ] D05 LINE 語音 | 錄「這是運動筆記；下週五下午三點跑步三十分鐘；這不是已完成的任務」 | 3 檔；逐字稿保留關鍵數字、時間與否定語意；摘要不冒充完整逐字稿 |
| [ ] D06 LINE 圖片 | 傳一張含文字的圖片 | 只有原檔 1 檔；沒有 OCR／逐字稿／摘要；通知「原檔已保存，未解析」 |
| [ ] D07 LINE PDF | 傳小型 PDF | 只有原檔 1 檔；下載比對雜湊；不生成摘要、不呼叫附件解析 |
| [ ] D08 一般檔案 | 傳一個測試文字檔或 ZIP，使用檔案訊息 | 只存原檔、不解壓不執行；LINE 文字訊息與「文字檔附件」行為不同 |
| [ ] D09 原檔優先 | 在下節 F01 故障情境觀察 | 摘要失敗時原檔仍在 Drive；不能只看最後成功案例判斷順序 |
| [ ] D10 私人範圍 | 查 Drive 分享狀態、目錄與 `/files` 連結 | 未自動建立公開分享；每筆在設定的 UAT 根目錄內，沒有寫入正式 raw |
| [ ] D11 雲端與本機同步 | 先在 Drive 網頁確認，再在 Mac 同步目錄確認 | 分別記錄雲端完成及 Mac 可讀時間；通知不當成 Mac 同步證明 |

若語音轉錄不正確，先把 D05 記為失敗，不按「確認全文與摘要正確」。本版沒有線上逐字稿編輯流程；直接在 Drive 改檔後，原檔捷徑會因 SHA／版本不同停止，不能把 `/review` 當成自動調和外部編輯的工具。

Siri 的原始二進位上傳可用以下指令；使用自己的測試金鑰與路徑，不要把真實金鑰貼入驗收報告：

```bash
curl --fail-with-body -X POST http://localhost:8081/api/v1/note/ios \
  -H "X-API-Key: <填入UAT金鑰>" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @/path/to/uat-audio.m4a
```

## 5. 分類逐項驗收

每次更改模式都編輯 `.env.uat` 並 `vn_uat up -d --force-recreate web worker`，再用**新收件**測試，不期待舊資料自動搬家。

| 勾選／編號 | 設定與操作 | 通過條件 |
| --- | --- | --- |
| [ ] R01 明確主題 | `custom`＋`rules`，分別傳 `#finance`、`#fitness`、`#tech` 文字 | 三筆都進指定主題；不需分類 API key，摘要仍需要 Gemini |
| [ ] R02 停用分類 | `CAPTURE_CLASSIFICATION=none`，傳 `#finance UAT-R02` | 目錄直接是 `<root>/<年月>/<CID>`，API category 為 null |
| [ ] R03 Jev 語意分類 | 改 `custom`＋`jev`、填 JEV key；各主題準備至少 3 則不帶 # 的簡單文字 | 先由你寫下期望主題，再逐筆比對結果；誤判記為品質問題，不以「模型有回答」算通過 |
| [ ] R04 分類故障 | 保留 Gemini 有效，暫時清空 JEV key；傳不帶 # 的文字 | 原文／摘要仍完成，分類為 unclassified；不是整筆失敗。之後恢復 JEV key |
| [ ] R05 附件不做語意解析 | custom 下傳沒有 # 標記的圖片／PDF | 只有原檔，進 unclassified，不靠讀圖／讀 PDF 分類 |
| [ ] R06 附件明確主題 | 把檔名命名為 `#finance UAT-R06.pdf` 後作檔案訊息傳送 | 沿用允許的明確標記，分類 finance；仍只存原檔 |
| [ ] R07 可選 Gemini 分類 | 若要驗收供應商替換，改 `CAPTURE_ROUTER=gemini` 再跑三主題案例 | 合法主題或 unclassified，不產生新目錄代號；未測此模式就標「未驗收」 |

沒有校準過的分類準確率保證；保留原始測試句、你預先定義的期望與實際主題。不要只挑成功句，也不要把帶 # 的規則測試算成 Jev 語意分類測試。

## 6. 清理、校對與收回逐項驗收

使用可丟棄的不同收件測每一列，避免前一項刪除影響下一項。Drive UI 的垃圾桶是真實外部證據；資料夾仍存在不代表檔案沒被清掉。本版只處理登記內容，空資料夾可能保留。

| 勾選／編號 | 操作 | 通過條件 |
| --- | --- | --- |
| [ ] C01 查詢 | `/files CID`，與 API detail 比對 | F 代號、角色、大小與狀態一致；已移除的檔案不再列有效下載連結 |
| [ ] C02 未校對禁止捷徑 | 對完成但未 review 的語音輸入 `/clear CID original` | 拒絕捷徑，沒有移除任何內容 |
| [ ] C03 人工校對 | 閱讀完整逐字稿與摘要，再 `/review CID` | reviewed_at 有值，沒有刪檔；LINE 提供預覽原檔清理的按鈕 |
| [ ] C04 預覽不刪除 | `/clear CID original`，先不確認 | 清單只有該音檔 F ID，列大小與影響；Drive 三檔都還在 |
| [ ] C05 取消 | 點取消，再嘗試該次確認 | 不刪任何檔；舊 token 不能重新啟動清理 |
| [ ] C06 只清音檔 | 新預覽後確認 | 音檔在垃圾桶；transcript＋note 保留；note 記錄原檔狀態／時間／原因並移除死連結 |
| [ ] C07 指定單檔 | `/clear file F代號`，選圖片或某筆 note | 預覽並確認後只移該檔；可明確移除唯一內容，不連帶刪其他筆 |
| [ ] C08 跨筆指定清單 | `/clear file F第一筆 F第二筆` | 只列所選 F IDs、去重計數；確認後只處理這份清單 |
| [ ] C09 整筆清理 | `/clear CID all` | 該筆所有登記檔進垃圾桶，狀態 deleted；其他來源不受影響，暫存內容撤下 |
| [ ] C10 已校對批次 | 準備已校對／未校對語音各一筆及圖片一筆，先用 API detail 確認測試語音 `category=fitness`；`/clear reviewed topic=fitness before=2099-01-01` | 只選符合主題、UTC 日期條件且全文摘要仍完整的已校對語音原檔；圖片與未校對語音排除 |
| [ ] C11 確認過期 | 預設等 600 秒，或測試環境把 DELETION_PREVIEW_SECONDS 設 30 後重新預覽；超時再確認 | 拒絕執行，要求新預覽；沒刪檔。完成後還原設定 |
| [ ] C12 清單內容變更 | 預覽後，在 Drive 編輯該檔，再確認 | 拒絕舊清單或逐檔失敗停止；沒有把變更當作原授權，另記實際狀態 |
| [ ] C13 保留全文失效 | 已 review 的另一筆語音先預覽 original，再把 transcript 移至垃圾桶，然後確認 | 停止，原音檔仍保留；不能因只選音檔就忽略已失去全文。測完人工還原全文 |
| [ ] C14 確認重放 | API 保存同一 token，第一次成功後再 confirm 相同 token | 回已有結果，沒有第二次外部清理；不要用新建預覽來冒充重放測試 |
| [ ] C15 範圍不明 | `/clear CID`、未知 `/clear` 指令 | 提示需明確範圍，沒有刪除；普通文字不會被模型轉成刪除命令 |
| [ ] C16 完成後 LINE 收回 | 收件完成後，在 LINE「收回」原始訊息 | 不再要求確認，該筆管理內容撤下，Drive 進垃圾桶；手機「刪除聊天訊息」不等於收回事件 |
| [ ] C17 處理中收回 | 新傳測試 PDF／語音後立刻收回；等 worker 處理／重試結束 | 可以沒有建立 capture，或留下 deleted 去重紀錄；不可因舊任務重建有效內容；已在途／已送達通知不代表內容仍存在，以最終撤下狀態為準。精確競態另以離線測試驗證 |
| [ ] C18 刪後不重建 | 對整筆已刪 CID 用 API retry，並等舊佇列工作結束 | API 409；沒有新建原檔／摘要。檔案 ID 不因重試改變 |
| [ ] C19 非受管內容 | 在 UAT 根目錄另外手動建立參照檔；完成單筆／批次清理 | 參照檔不動；不是掃描整個根目錄刪檔 |
| [ ] C20 搬出允許根目錄 | 將另一筆受管資料夾搬到設定根目錄外，再要求預覽／清理 | 停止該項；不跟到外面刪除，也不自動搬回 |

用 API 測清理可直接在 Swagger 依序呼叫：

```text
POST /api/v1/deletions/preview
{"scope":"files","file_ids":["F實際代號1","F實際代號2"]}

POST /api/v1/deletions/confirm
{"token":"上一步真正回傳的token"}

POST /api/v1/deletions/cancel
{"token":"另一份尚未確認的預覽token"}
```

在 Swagger 選對路由後，只把對應的 JSON 填入 body。`original`／`all` 用 `capture_id`；`reviewed` 的 API 篩選欄位是 `category`／`before`，LINE 指令才使用 `topic=`。確認／取消請選不同預覽分別測，不要期待已執行的清理可以用 cancel 復原。

C10 的語音可在 Jev／Gemini 模式準備，確認實際分類後才做批次測試；若測試資料屬於另一主題，就改用該主題。rules 模式不會只因語音談到「運動」就判為 fitness；沒有符合條件的資料時，空清單是正確結果。

## 7. 故障恢復與持久化

只在隔離環境故障注入。每次只改一個因素，恢復後先查原 CID，若自動重試已完成就不用再送一次。**重新 POST 原文是另一筆 HTTP 收件，不是重試。**

| 勾選／編號 | 故障注入方式 | 通過條件 |
| --- | --- | --- |
| [ ] F01 摘要失敗 | 暫時清空 UAT Gemini key，重建 web／worker，再以 HTTP 傳新文字 `#finance UAT-F01：預算1000元，尚未購買ETF。` | 原檔先成功在 Drive；status failed，沒有假摘要或 complete；有 LINE 時通知「原檔已保存，文字處理尚未完成」 |
| [ ] F02 恢復摘要 | 恢復 key、重建兩服務，對同一 CID 用 `POST /api/v1/captures/{capture_id}/retry` | 沿用同一個原檔 cloud_id，最後只有 2 檔，內容正確；不另外建立來源 |
| [ ] F03 通知失敗 | UAT LINE token 改成明顯無效的測試值；重建服務，改由 HTTP 送新文字 | Drive 內容 complete，但 notified=false；不能只用「沒收到 LINE」判斷保存失敗 |
| [ ] F04 恢復通知 | 還原有效 LINE token、重建後 retry 原 CID | 收到通知，notified=true；雲端檔案數及 ID 不變，不重新轉錄／摘要 |
| [ ] F05 QueueUnavailable | `vn_uat stop redis` 後經 HTTP 送新文字；若 client 先逾時，先恢復 Redis 再用 `GET /api/v1/captures` 找當次來源，不盲目重送 | 原文已在私人暫存；正常回覆為 503＋capture_id。還原 `vn_uat start redis`，對已登記 CID retry，最後內容正確 |
| [ ] F06 正常重啟 | 記錄已完成／已刪 CID 與 cloud IDs，`vn_uat restart web worker redis` | 原紀錄與去重標記還在，不額外建立雲端副本；已刪內容不重生 |
| [ ] F07 停機後恢復 | `vn_uat down` 後再次 `vn_uat up -d`；**不加 -v** | SQLite 與 Redis volume 保留；原 CID 可查，已刪內容仍不重生 |
| [ ] F08 Drive 尚未配置 | 在尚無 Web 連接的新 UAT，保留 Drive token／folder ID 空白，經 HTTP 收新文字 | 可先接受本機來源，但最後 failed，不能聲稱已存入 Drive；完成 Web 授權後 retry 同一 CID。既有 Web 連接不會因環境 folder ID 空白而消失，請勿刪除既有 DB 冒充未配置 |

原檔／逐字稿／摘要已可靠寫入且不需重試後，`data-uat/captures/<CID>/` 應無內容檔；鎖與 SQLite 管理紀錄仍會保留。pending／failed 內容及失敗的 note metadata 待傳稿可以留在私人暫存，不能把「非空」一律當漏清理；這版沒有按天自動清除失敗來源。

以下情境以現有離線測試作主要證據，真實帳號沒有可控故障注入時標「離線通過／真實未覆蓋」，不要手動破壞正式服務或宣稱已完成真實驗證：

- 首塊／最後回應遺失、server offset 落後及續傳 session 404／410：`test_drive_resumption.py`。
- 部分檔案成功、另一檔權限失敗後只重試未完成項：`test_full_cleanup_tombstone_and_failed_subset_retry`。
- note metadata 失敗、通知重試不清掉待傳稿、外部編輯不被覆寫：`test_pending_metadata_survives_queued_completion_and_confirmation_retry`、`test_metadata_retry_refuses_external_note_edits`。
- 已開始上傳與刪除意圖同時到達、晚到內容不得重生：`test_inflight_upload_after_intent_is_removed_only_when_bytes_match_preview`。
- LINE 平台重送同一事件：`test_durable_ingestion_download_and_redelivery`、`test_queue_failure_keeps_source_and_redelivery_reuses_it`。手機重新發送一樣文字有新 message ID，應建立新收件，不能當去重測試。
- 殺死 worker／主機斷電等硬故障不等於正常 restart；Redis AOF 的最後一秒仍可能遺失，應另做還原演練再標通過。

## 8. Notion 與 BYOK 相容驗收

Drive 主線完成後才做這組。保留 UAT Drive 設定以便回查，將 `.env.uat` 的 `CAPTURE_DESTINATION` 改成 `notion`、填測試 Notion token 並授權可被頁面樹辨認的專用頂層測試根頁，重建 web／worker。不把正式筆記當測試目標。

| 勾選／編號 | 操作 | 通過條件 |
| --- | --- | --- |
| [ ] N01 文字全文＋摘要 | HTTP／LINE 各傳一段多行文字，含獨特開頭與結尾 | 在 Notion 都能讀到完整原文、摘要與 CID／來源 metadata；不是只有摘要；不另寫一份 Drive |
| [ ] N02 語音 | LINE／Siri 各傳短語音 | Notion 有完整逐字稿＋摘要，沒有音檔附件；完成後本機內容暫存清除 |
| [ ] N03 拒收附件 | LINE 傳圖片及 PDF | 明確回覆 Notion 不收附件；沒有新 Notion 附件，也不偷偷改存 Drive |
| [ ] N04 建立／追加頁面 | 觀察至少一次建立新頁與一次追加既有主題 | 兩條實際路徑都有正確全文／摘要與各自 CID；未走到的分支標「未覆蓋」，不要改 DB 硬塞路由 |
| [ ] N05 共用頁收回 | 同頁放兩筆不同 CID，收回其中一筆原始 LINE 訊息 | 只撤下該筆區塊，另一筆保留；不封存整個共用主題頁 |
| [ ] N06 外部改動保守停止 | 把被測來源區塊搬移，或在受管區塊下另外加入子內容，再收回 | 維持 delete_pending／回報不確定，不假稱全部撤下；外加內容不被連帶封存 |
| [ ] N07 BYOK 寫入相容 | 切回 Drive 為主要目的地；Swagger `POST /api/v1/note` 使用測試的單一 `X-Voice-Notion-Config` JSON header，不填 X-API-Key | 音訊只寫 BYOK 測試 Notion，不寫部署者 Drive；回應保留 task_id／capture_id |
| [ ] N08 BYOK 管理隔離 | 只帶 BYOK header 呼叫下方列出的管理路由 | 401，不能讀或刪部署者資料；不要把 admin key 同時放進請求 |

BYOK header 內欄位為 `X-Gemini-Api-Key`、`X-Notion-Token`，可另加 `X-Line-Token`／`X-Line-User-ID`；只在私人 Swagger／客戶端填寫，不把真實憑證保存到報告。未配置 BYOK 或 Notion，就把 N 組標為「未驗收」，不能因 Drive 成功而勾選相容性通過。

N08 要分別測 `GET /api/v1/captures`、`GET /api/v1/captures/{capture_id}`、`POST /api/v1/captures/{capture_id}/review`，以及 `POST /api/v1/deletions/preview`、`POST /api/v1/deletions/confirm`、`POST /api/v1/deletions/cancel`。使用測試 CID 與符合 schema 的 body，才能確認是授權拒絕而非格式錯誤。

## 9. 授權與隱私驗收

| 勾選／編號 | 操作／證據 | 通過條件 |
| --- | --- | --- |
| [ ] S01 個人 API 金鑰 | Swagger／HTTP 不帶 key、帶錯 key，再帶正確 UAT key | 私人路由前兩者 401；正確金鑰才可操作。health 與 Swagger 可公開，不以它們判斷管理授權 |
| [ ] S02 偽造 LINE webhook | LINE 已配置時，不帶簽章 POST `{"events":[]}` 到 webhook | 401，沒有收件。若回 503 是尚未配置，不能算驗簽通過 |
| [ ] S03 LINE 私聊白名單 | 測試第二個使用者私聊；測試 Bot 若允許群聊，再於群組傳訊息 | 不新增收件、不觸發模型或刪除；群聊未能實測時記離線測試證據，不假填通過 |
| [ ] S04 格式與大小 | HTTP 傳假音訊，另傳超過 CAPTURE_MAX_BYTES 的測試檔 | 假音訊 400、超限 413，沒有可處理的新來源；副檔名不是格式驗證依據 |
| [ ] S05 日誌 | 用獨特、無敏感性的句子完成一筆，私下檢視 `vn_uat logs --since 10m web worker` | 不含原文首行／全文／逐字稿、API key、refresh token；task 成功回傳只含 status／capture_id |
| [ ] S06 資料範圍 | 檢查 UAT volume、Drive 根目錄、Notion 測試頁與設定 | 沒有寫入正式 data 或 raw；只用測試 Channel；下載連結不建立公開權限 |

不需要貼出真實金鑰來證明「未出現在日誌」。只保存檢查結論與遮罩後證據；不要提交 `.env.uat`、`data-uat`、LINE 原始 webhook 或帶憑證的 request headers。

## 10. 如何判定驗收完成、如何留下紀錄

每列記錄：`項目ID／測試版本／設定模式／CID與FID／期望／實際／證據／結果／問題`。結果只用「通過、失敗、未執行、未覆蓋」，不要把未做的真實測試改填為通過。

可複製這個格式到自己的私人驗收紀錄（不含金鑰／原文敏感資料）：

```text
驗收日期：
測試 commit：
環境／目的地／Router：
項目 ID：
來源 CID／檔案 FID：
期望結果：
實際結果：
證據：API狀態截錄、Drive／Notion畫面、檔案數、SHA256、通知畫面
結果：未執行
問題與重測結果：
```

第一次可先完成 D01／D02／D03／D05／D06／D07、R01、C02–C06、C09、C16、F01／F02／F06、S01／S02。這是核心路徑初驗，**不是全部功能驗收**。

正式採用 Drive 個人主線前，應完成 D／R（啟用的 Router）／C／F／S 組；危險競態有離線證據，真實未覆蓋部分明確列出。若要聲稱保留 Notion／BYOK 相容，也必須完成 N 組；只打算先啟用 Drive，可先記 N 組未驗收。

最後停止驗收環境：

```bash
vn_uat down
```

不要加 `-v`、不要清空帳號垃圾桶。若確定不要測試收件，先用本服務只針對 UAT CID 的清理流程；測試 Channel、Drive 根目錄與資料夾的後續保留由你決定。把發現的問題連同項目 ID、版本與 CID 回報，不貼私人憑證。
