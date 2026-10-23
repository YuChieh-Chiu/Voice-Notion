# 🌈 Voice-Notion 快速試用指南 (Demo Guide)

Demo 頁用自己的 Gemini／Notion 金鑰產生 Siri 捷徑設定，目的地為自己的 Notion。使用可信任的已部署服務即可免自架；Google Drive 個人部署請走 [管理者授權流程](DEPLOYMENT_GUIDE_ADMIN.md#drive-web-oauth從-google-console-到可測試)。

---

## 快速開始三部曲

### 1. 取得 API 金鑰
- **Google Gemini**: 前往 [Google AI Studio](https://aistudio.google.com/app/apikey) 建立 API key，並確認模型額度及帳務可用。
- **Notion**: 前往 [My Integrations](https://www.notion.so/my-integrations) 建立整合，並**授權**給您的目標頁面。

### 2. 使用展示網頁設定
- 開啟你信任的部署服務的 `/demo`；本機 UAT 為 `http://localhost:8081/demo`。
- 填入 Gemini／Notion 金鑰；LINE 通知選填，但 access token 與 user ID 需成對填寫。
- 按「產生捷徑設定 JSON」。預覽預設遮蔽，複製會取得含完整金鑰的純文字 JSON；它沒有加密。自動複製失敗時可使用頁面提供的手動選取複製。
- 修改欄位會使舊 JSON 失效，請重新產生。產生成功不代表金鑰已驗證、Notion 已授權或捷徑已安裝。

### 3. 設定 iOS 捷徑
- 參考 [Siri 捷徑快速設定指南 (Demo 版)](./SIRI_INTEGRATION_DEMO.md) 將配置貼入您的 iPhone。

---

## ⚠️ 試用限制與聲明
- **速率限制**：因為部署資源有限，展示模式下的 IP 速率限制為每小時 3 次請求。
- **設定頁隱私**：頁面使用本地資產，只在瀏覽器產生設定；不自動傳送金鑰，也不寫入 localStorage／sessionStorage。清除或離頁會清除頁內設定，已複製的剪貼簿內容需自行移除。
- **執行捷徑**：金鑰與錄音會傳至你設定的服務；憑證可能為背景工作及重試加密暫存，原音檔與文字也由服務處理。請確認部署者與資料處理方式。Notion 只保存完整文字／逐字稿與摘要，不帶附件。

---

## 🚀 想要擁有自己的伺服器？
如果您想移除限制並自架系統，請參考 [管理者部署指南 (Admin Guide)](./DEPLOYMENT_GUIDE_ADMIN.md)。
