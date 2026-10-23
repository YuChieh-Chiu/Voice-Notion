# Note-Goblin 個人部署與收件操作

本版支援 Siri／HTTP／LINE 共用收件，主要目的地選 Google Drive 或 Notion。產品決策見 [實作計畫](MULTI_SOURCE_CAPTURE_PLAN.md)，討論頁的示範選擇不會修改服務設定。

## 安裝與持久化

同一主機部署 web、worker、Redis；web／worker 必須共用 `./data:/data`，包括 SQLite、來源暫存與跨程序鎖。不能用不支援 SQLite／flock 語意的網路磁碟，也不能把兩個獨立 data volume 當成同一實例。備份時停止寫入或使用 SQLite backup API，不可只複製活躍的 `.sqlite3` 而漏掉 WAL。

```bash
cp .env.example .env
chmod 600 .env
mkdir -p data
chmod 700 data
openssl rand -hex 32
```

將隨機值填入 `SIRI_API_KEY`。在安裝依賴的 Python 環境產生 Fernet 金鑰並填入 `TASK_ENCRYPTION_KEY`：

```bash
cd backend
poetry install --only main,dev
poetry run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

web 與 worker 使用相同固定 Fernet 金鑰，DEBUG 也不產生臨時金鑰。輪換前先處理完加密事件與 BYOK 工作；遺失舊金鑰就無法解密既有待處理內容。

Compose 只把 nginx 綁在 `127.0.0.1:8080`，請於前方配置 HTTPS reverse proxy／受控 tunnel，並把正式網域加入 `ALLOWED_HOSTS`。Redis 不對主機公開，AOF 每秒同步且使用獨立 volume；最末一秒的佇列在硬故障下仍可能遺失，永久來源紀錄在 SQLite，可依 ID 重試。不要以 `docker compose down -v` 清理正式資料。

內層 nginx 透過 Docker DNS（`127.0.0.11`）動態解析 `web`，讓重建 web 後的 IP 變動可自行恢復。開源 nginx 需 **1.27.3 以上**，搭配 upstream shared zone；目前 Compose 使用 `stable-alpine`，更新映像後先執行 `nginx -t`。獨立隔離測試在 1.30.6 驗證實際 IP 更換後約 8.36 秒恢復，nginx 無需重啟；這不是零停機或五秒內恢復的保證。[nginx 官方說明](https://nginx.org/en/docs/http/ngx_http_upstream_module.html#server)

```bash
# 在專案根目錄，填妥 .env 後由操作者啟動
docker compose up -d --build
```

映像使用追蹤的 Poetry lock；web 只裝 main，worker 裝 main＋worker，初始化工具與測試不進映像。worker 預設 concurrency 1，STT 模型首次載入會由 faster-whisper 下載至 `/data/models`。離線回歸不下載模型或呼叫真實服務；後續經使用者授權的隔離 UAT 已下載模型，並呼叫真實 Drive、Jev、Gemini 與 LINE 推播，範圍及結果見驗收指南。

## 選擇目的地

| 設定 | 何時需要 |
| --- | --- |
| `CAPTURE_DESTINATION=drive` | 新部署使用 Web OAuth：client ID／secret＋固定 callback；token／根目錄加密保存在共用 SQLite，不要求 Notion token |
| `CAPTURE_DESTINATION=notion` | 使用 Notion，填 `NOTION_TOKEN` 並授權頁面；不要求 Drive OAuth |
| `GEMINI_API_KEY` | 文字／語音摘要；Notion 的頁面路由也使用 Gemini |
| `LINE_CHANNEL_ACCESS_TOKEN`、`LINE_USER_ID` | LINE 推播；可留空關閉通知 |
| `LINE_CHANNEL_SECRET` | LINE 收件／按鈕／收回驗簽；啟用收件時三項 LINE 設定都填 |
| `CAPTURE_DATA_DIR` | 容器用 `/data`，兩個程序指向同一私人持久化目錄 |

Drive 文字兩檔，語音三檔；圖片／PDF／一般檔案只存一個原檔，不解析、不送模型。Notion 只存全文／逐字稿與摘要；拒收圖片與檔案，不改存 Drive。切換目的地只影響新收件，沒有舊資料搬移。

### Drive Web OAuth：從 Google Console 到可測試

本機及 Linode 使用同一套 `/oauth/google/` 程式；本機 callback 用 localhost，Linode 用正式 HTTPS。以下先完成獨立 UAT；本機通過不代表 Linode 的目標 CPU 平台、外層代理與持久化已驗收。

1. 在 Google Cloud Console 新建或選擇專案，啟用 **Google Drive API**。
2. 到 **Google Auth platform → Branding** 建立同意畫面。個人 Gmail 選 **External**；測試時在 **Audience → Test users** 加入稍後登入的 Google 帳號。
3. 在 **Data Access** 加入且只要求 `https://www.googleapis.com/auth/drive.file`。
4. 在 **Clients → Create Client** 選 **Web application**。截圖中的 **「已授權的 JavaScript 來源」留空**，在 **「已授權的重新導向 URI」** 新增：

   ```text
   http://localhost:8081/oauth/google/callback
   ```

   `http`、`localhost`、`8081` 與路徑必須完全一致，不加末尾斜線，也不要改用 LINE webhook。Linode 建立獨立 Web client，使用 `https://你的網域/oauth/google/callback`。
5. 建立後下載 JSON 到私人位置；禁止提交 Git。以私人編輯器將 `web.client_id`、`web.client_secret` 填入測試用 `.env.uat`；不要把 JSON／secret 貼到聊天或 issue。新部署設定如下，保留既有的 `SIRI_API_KEY`／`TASK_ENCRYPTION_KEY`：

   ```dotenv
   CAPTURE_DESTINATION=drive
   DRIVE_CLIENT_ID=<Web client ID>
   DRIVE_CLIENT_SECRET=<Web client secret>
   DRIVE_OAUTH_REDIRECT_URI=http://localhost:8081/oauth/google/callback
   DRIVE_FOLDER_ID=
   DRIVE_REFRESH_TOKEN=
   ```

6. 按 [驗收指南](TESTING_GUIDE.md) 建立 UAT 私人目錄與設定，於 repo 根目錄啟動：

   ```bash
   docker compose -p voice-notion-uat --env-file .env.uat -f docker-compose.uat.yml up -d --build
   ```

7. 用**同一瀏覽器**開啟 `http://localhost:8081/oauth/google/setup`。從私人 `.env.uat` 複製 `SIRI_API_KEY` 的值，貼到「部署者管理金鑰」，新資料夾填 `Voice-Notion-UAT`，按「前往 Google 授權」。網頁不會把管理金鑰存入 localStorage／cookie。
8. 在 Google 登入已加入 Test users 的帳號，核對 app 與權限後同意。不要從聊天連結手動拼接 callback；它必須帶同一瀏覽器的 session。回到無 code 的完成頁後，請回報「已授權」即可，不分享完整 callback 網址。
9. 可在連線頁重新輸入管理金鑰按「查看連接狀態」；或帶 `X-API-Key` 呼叫 `GET /oauth/google/status`。`connected=true` 才代表 token 與專用私人根目錄已完成。status 不回傳 access／refresh token。

授權後的 refresh token、Google 帳號識別與固定根目錄 ID 以 Fernet 加密保存在共用 SQLite，web／worker 每次操作讀取同一連接；不用手動抄 refresh token，也不用為此重建容器。需要保留同一 `TASK_ENCRYPTION_KEY`、OAuth client 與私人 data volume。只有變更 `.env.uat` 才需 `up -d --force-recreate web worker`。

連接頁使用自託管資源；owner 驗證先於 OAuth JSON body 解析。state 限時且一次性，搭配瀏覽器 HttpOnly cookie 與 PKCE。成功 callback 轉往不含 code 的完成頁；Compose、Dockerfile 及內層 nginx 不記 callback 查詢字串。**Linode 外層 HTTPS proxy、CDN 或 WAF 的 access／error 日誌也必須遮罩或停記 callback query；本機設定不能替代此檢查。**

常見狀況：

- Google `redirect_uri_mismatch`：檢查 Console 與 `DRIVE_OAUTH_REDIRECT_URI` 的 scheme、主機、port、路徑及尾斜線。
- Google `access_denied`：核對登入帳號是否列在 Test users，以及 Drive API／同意畫面設定。
- 連線頁 401：管理金鑰應為本環境的 `SIRI_API_KEY`，不是 Google client secret。
- 回呼 400：session 過期、換瀏覽器、取消或重放；回連線頁重新按授權。
- 根目錄建立途中故障：按「重試完成資料夾設定」，沿用已保存的固定 ID；若 pending token 已失效，重新授權仍保留原帳號與根目錄。
- 重新授權 409：不允許改成另一 Google 帳號／client 或默默替換既有受管根目錄；先保留原設定再規劃遷移。

External＋Testing 的這類 refresh token 在 7 天後到期；需要重新授權。長期運行前另確認 OAuth app 發布與帳號政策。[Google 到期規則](https://developers.google.com/identity/protocols/oauth2#expiration)

**接續測試的依賴：** 完成 Web 授權即可驗證 Drive token 刷新、固定根目錄、檔案上傳／讀回／垃圾桶與重試；文字摘要另需 `GEMINI_API_KEY` 及可用額度。Jev 語意分類另需 `JEV_API_KEY`，可先用 rules。LINE 純推播只需 access token 與 user ID；收件／指令／按鈕／收回才另需 Channel secret 及公開 HTTPS webhook。只完成 Drive 時，其他功能仍標為未驗收。

服務只要求 `drive.file`；**填入任意 folder ID 不會取得權限**。新流程建立自己的私人根目錄；可在 Drive 手動移到 Atlas 下並保留 ID。既有目錄須先經同一 app 的檔案授權流程，本版沒有 Picker UI。目標不能是 anyone／domain 分享，不建立公開連結。

舊部署已配置 `DRIVE_FOLDER_ID`／`DRIVE_REFRESH_TOKEN` 的方式仍可運作；`scripts/drive_oauth.py` 是舊 Desktop 初始化相容工具，不是上述 Web 流程。已有受管雲端檔案不會自動遷移或覆寫授權；請勿直接清空舊設定／DB 來重新初始化。

Atlas/raw 是一般 Drive 根目錄，目錄為 `<root>/<主題可省略>/<年月>/<C代號>/`。Atlas 的規則、索引、wiki 不由本服務修改。**雲端保存成功不表示 Mac 同步完成**。

### 自訂主題

依你的示範偏好可在 `.env` 明確填入：

```dotenv
CAPTURE_DESTINATION=drive
CAPTURE_CLASSIFICATION=custom
CAPTURE_ROUTER=jev
CAPTURE_CATEGORIES='[{"id":"finance","description":"投資理財"},{"id":"fitness","description":"運動"},{"id":"tech","description":"AI 技術知識學習"}]'
```

另填 `JEV_API_KEY`；主題可附 `folder_id` 映射至已授權私人目錄。`none` 停用分類；`rules` 只用明確 `#finance` 等標記；`gemini` 可作分類供應商。未知或供應商故障進 `unclassified`，不讓模型指定目錄或 URL。Jev 會收到文字／逐字稿；Gemini 收到摘要所需文字。附件位元組不送這些模型，原文中的網址也不由本服務任意抓取。

## LINE 收件、校對與清理

將 webhook 設為 `https://你的網域/api/v1/line/webhook`，啟用 webhook 與 redelivery，關閉會造成重複回覆的預設自動回應。Channel secret 用於原始 body HMAC 驗簽；只有設定的 `LINE_USER_ID` 私聊會處理，群組與其他使用者忽略。文字、語音、圖片與檔案使用固定官方內容端點，拒絕外部內容提供者與跳轉。

建議同時開啟 **Webhook redelivery** 與 **Error statistics aggregation**：前者在接收失敗時重送，後者記錄錯誤原因及統計；不保證每次都能重送，也不會補回開啟統計以前的紀錄。[LINE 重送](https://developers.line.biz/en/docs/messaging-api/receiving-messages/)、[錯誤統計](https://developers.line.biz/en/docs/messaging-api/check-webhook-error-statistics/)

若聊天室出現「本帳號無法個別回覆」等預設訊息，到 **LINE Official Account Manager → 設定 → 回應設定** 關閉自動回應訊息，保留 Webhook。這是 LINE 的另一套自動回應，與本服務的保存完成通知分開設定。[LINE 官方說明](https://developers.line.biz/en/docs/messaging-api/building-bot/)

若聊天室只有文字、沒有圖片／附件按鈕，優先查看 **Official Account Manager → 設定 → 帳號設定 → 功能使用 → 照片／影片接收**，改為允許接收（選項名稱以介面為準），再退出並重新進入聊天室。官方說明指出，選擇不接收會關閉圖片、影片、語音、聯絡人、位置與檔案的傳送功能；不用因此推論必須開啟人工 Chat 模式。[LINE 官方帳號設定手冊](https://www.lycbiz.com/jp/manual/OfficialAccountManager/account-settings_basic/)

未公開好友連結、QR Code 或關閉搜尋顯示，只能減少被找到的機會。真正的服務權限由 `LINE_USER_ID` 白名單加上 HMAC 驗簽控制；此值是 Messaging API 的 `userId`，不是暱稱或可搜尋 LINE ID。webhook 與 worker 都檢查私聊及同一 userId；不符合的事件不進入收件處理或寫入部署者 Drive，也不觸發模型。這限制的是本服務處理，不能禁止別人在 LINE 平台傳訊或關閉 LINE 自身的自動回應。臨時 gateway 若啟用診斷，仍可能留下事件 ID／種類等 metadata，與原文、附件內容分開。

`data-uat/models` 是 UAT worker 的 Whisper 語音辨識模型快取；Docker worker 以 `HF_HOME=/data/models` 持久化下載。本輪 small 模型快取約 466 MiB，後續語音可重用；重新建立快取需網路及下載時間。收件原檔、逐字稿與 SQLite 在 `data-uat/captures`，不要將它們當模型快取刪除。

| 指令 | 行為 |
| --- | --- |
| `/files C代號` | 查狀態、檔案 ID 與私人連結 |
| `/review C代號` | 人工確認全文與摘要；不刪除 |
| `/clear C代號 original` | 只清已校對、全文與摘要仍完整的語音原檔 |
| `/clear file F代號 [F代號…]` | 明確清指定單檔或跨筆批次，可移除唯一內容 |
| `/clear C代號 all` | 清該筆全部服務管理檔案及暫存 |
| `/clear reviewed topic=fitness before=2026-10-01` | 批次清已校對語音原檔，日期為 UTC 收件日且不含指定日；篩選可省略 |

先顯示確切檔案、大小與損失，再按「確認移到垃圾桶」或「取消」。確認預設 600 秒、最多 100 檔，綁定部署者與當次清單。清單太長請縮小主題／日期；省略範圍不猜測。文字原文、圖片與 PDF 沒有可依「保留全文＋摘要」捷徑清掉的額外附件，請明確用 file／all。

捷徑會驗證保留的全文／摘要與已校對版本一致，確認前及取得工作鎖後再檢查。外部移動／修改、已移到垃圾桶或狀態改變，都會停止而要求新預覽。請避免在清理執行期間手動移動、編輯受管檔案；雲端外部操作無法和 SQLite 組成同一交易。

第一版沒有永久刪除、清空垃圾桶或自動按天清理。**垃圾桶仍占容量**，永久刪除由你在 Drive 操作。部分失敗逐檔回報；對同一確認再點一次，只重試尚未完成項目。若筆記來源狀態更新失敗，已刪檔案不還原，待傳 note.md 私人稿保留，重試不覆寫外部編輯。

LINE 收回訊息會先持久化撤銷標記、停止排隊工作，再撤下服務管理內容，不需再確認。Drive 移至垃圾桶；Notion 只封存該筆區塊，自己建立的空頁可封存；有其他內容時只中性化未被外部改過的服務頁名。搬移、權限不足或不確定寫入不能假報完成。Atlas 衍生內容、其他另存副本與供應商備份不在撤下範圍。

## HTTP 管理與恢復

所有個人收件／管理路由都帶 `X-API-Key: <SIRI_API_KEY>`。`POST /api/v1/note` multipart、`POST /api/v1/note/ios` binary 保留既有回應欄位，另回 `capture_id`。文字用 `POST /api/v1/captures/text`、JSON `{"text":"完整原文","source_url":"https://example.org/source"}`；source_url 可省略，只記錄、不抓取網址內容。

- `GET /api/v1/captures?limit=100&offset=0`、`GET /api/v1/captures/{id}` 查紀錄。
- `POST /api/v1/captures/{id}/retry` 重排已保存工作，刪除後拒絕重建。
- `POST /api/v1/captures/{id}/review` 人工校對。
- `POST /api/v1/deletions/preview`：`{"scope":"file","file_id":"F..."}`，或 `{"scope":"all","capture_id":"C..."}`，跨筆指定清單用 `{"scope":"files","file_ids":["F...","F..."]}`，或 original／reviewed 搭配 category／before。
- `POST /api/v1/deletions/confirm`、`POST /api/v1/deletions/cancel`：`{"token":"預覽回傳值"}`；GET 不刪除。

BYOK 仍限自己的 Notion，透過單一 `X-Voice-Notion-Config` JSON header 提供 `X-Gemini-Api-Key`／`X-Notion-Token` 及選用 LINE 欄位；不接受 BYOK 進入個人管理或部署者 Drive。broker 只傳來源／事件 ID；BYOK context 與待處理 LINE 事件在私人 DB 加密。

故障處理：

1. QueueUnavailable：內容先保存在 `/data`；依 `capture_id` 呼叫 retry，不重送產生另一筆 HTTP 收件。
2. LINE 事件已保存但未排入佇列：webhook 回 503，redelivery 會沿用事件與來源 ID。若 Redis 最末佇列遺失，可由操作者以事件 ID 重排 `app.worker.tasks.process_line_event`；不要把原文或 token 放入 broker。
3. Drive 原檔成功、STT／摘要失敗：通知會明確標示未完成；保留原檔與可重用暫存，修正供應商設定後重試。
4. Notion 寫入回應遺失：依 CID／part marker 查找；找到就復用。新建頁使用含 CID 的唯一頁名恢復。若找不到、區塊移動或外部修改，就維持 failed／delete_pending。先在 Notion 核對，不可清除 pending checkpoint 後盲目重寫。
5. note metadata 外部編輯衝突：API 的 `metadata_errors`／LINE 會指出未完成來源狀態。保存雲端編輯與私人待傳稿，人工將來源已移除與連結狀態合併至雲端；備份 `/data` 後由操作者調和 DB 的該 note checksum／pending checkpoint 與確認清單狀態。不提供自動覆寫或放寬驗證的管理按鈕。
6. Drive 權限不足、受管檔案搬出根目錄或確認後外部變更：停止該項，不掃描／清理整個 raw。先恢復允許權限或人工核對，再處理既有意圖；不要直接刪資料庫以重試。

失敗暫存不按天自動刪除；恢復後成功會清理，同筆整筆撤下也會清理。需要丟棄 Drive 失敗收件可明確用 all。Notion 失敗收件需先核對遠端再由操作者清理私人暫存；不要刪除 dedup 標記後重新排舊事件。備份、Redis AOF 與 Drive 垃圾桶仍可能含舊內容，並非永久抹除。

## 離線驗證

逐項操作、通過條件、隔離環境與驗收紀錄見 [測試與驗收指南](TESTING_GUIDE.md)。

```bash
cd backend
poetry install --only main,dev,setup
PYTHONPATH=. poetry run pytest -q
poetry run ruff check app scripts/drive_oauth.py tests
poetry check --lock
```

測試以暫存目錄、假憑證與 HTTP／SDK 替身執行，不載入私人 `.env`，不呼叫模型、Drive、Notion、LINE。部署後仍須由操作者驗證 OAuth refresh、資料夾授權、Notion 頁面權限、LINE 真實 redelivery／收回與 `/data` 持久化。

官方依據：[Drive scope](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)、[Google Web OAuth](https://developers.google.com/identity/protocols/oauth2/web-server)、[舊 Desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)、[續傳](https://developers.google.com/workspace/drive/api/guides/manage-uploads)、[垃圾桶](https://developers.google.com/workspace/drive/api/guides/delete)、[LINE 簽章](https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/)、[LINE 重試](https://developers.line.biz/en/docs/messaging-api/retrying-api-request/)、[Notion block archive](https://developers.notion.com/reference/delete-a-block)。
