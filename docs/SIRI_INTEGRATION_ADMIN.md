# 🛠️ Siri 完整流程整合指南 (管理者個人版)

> [!IMPORTANT]
> **本文件適用於自架伺服器並使用個人 `SIRI_API_KEY` 的管理者。**
> 如果您是透過 [展示頁面](https://voice-notion.jacktoholiday.uk/demo) 進行試用，請改點擊 [試用者 Siri 設定指南 (Demo Siri Guide)](./SIRI_INTEGRATION_DEMO.md)。

本文件說明如何將 iOS 的 Siri (捷徑) 與您自架的 Voice Notion 整合，實現透過語音喚醒並自動上傳錄音至系統的功能。

## 1. API 規格

系統提供了兩個語音上傳端點：

### `/api/v1/note/ios` - iOS Shortcuts 專用

- **URL**: `http://<YOUR_SERVER_IP>:8000/api/v1/note/ios`
- **Method**: `POST`
- **Headers**:
    - `X-API-Key`: `<你的 SIRI_API_KEY>` ⚠️ **必須**
- **Body** (raw binary):
    - 音訊檔案二進位資料 (支援 m4a, wav, mp3)
    - 最大 25MB (約 20 分鐘錄音)

### `/api/v1/note` - 標準端點（測試用）

### 回應範例 (Success 202)
```json
{
  "message": "已收到，開始處理",
  "task_id": "c6109968-3855-4a6c-8431-12c8770df523"
}
```

### 外部存取 (ngrok Setup)
若要讓手機上的捷徑存取本機 (localhost) 伺服器，需透過 ngrok 將 localhost 暴露至外網。

1.  確認 ngrok 正在運行：
    ```bash
    ngrok http 8000
    ```
2.  取得公用 URL (HTTPS)：
    - 可在終端機查看，或瀏覽 `http://localhost:4040/api/tunnels`。
    - 範例：`https://2c9d5eb0ad00.ngrok-free.app`
3.  **捷徑設定時使用此 URL**：
    - 將 `http://<YOUR_SERVER_IP>:8000` 替換為你的 ngrok URL。
    - API 路徑不變，例如：`https://xxxx.ngrok-free.app/api/v1/note/ios`。

> [!NOTE]
> ngrok 免費版每次重啟 URL 都會改變，需重新更新捷徑設定。建議申請固定網域或升級方案。

## 2. iOS 捷徑 (Shortcuts) 設定步驟

請在 iPhone 上依照下列步驟建立捷徑：

1.  開啟「**捷徑 (Shortcuts)**」App，點擊右上角「+」建立新捷徑。
2.  **加入動作**：搜尋並加入「**錄製音訊 (Record Audio)**」。
    - 設定：品質可選「正常」或「非常高」。
3.  **加入動作**：搜尋並加入「**取得 URL 的內容 (Get Contents of URL)**」。
    - 將 `URL` 欄位設定為：`https://<你的ngrok網址>/api/v1/note/ios`
    - 點擊展開箭頭 (Show More)：
        - **方法 (Method)**：選擇 `POST`。
        - **Headers**：新增 Header
            - Key: `X-API-Key`
            - Value: `<貼上你的 SIRI_API_KEY>`
        - **要求回覆本文 (Request Body)**：選擇「**檔案 (File)**」。
        - **檔案 (File)**：選擇變數「**錄製的音訊**」(步驟 1 的產出)。
4.  **加入動作**：搜尋並加入「**取得字典值 (Get Dictionary Value)**」。
    - 取得 `message` 於 `URL 的內容` (步驟 3 的產出)。
5.  **加入動作**：搜尋並加入「**顯示通知 (Show Notification)**」或「**朗讀文字 (Speak Text)**」。
    - 將顯示內容設為步驟 4 取得的結果 (例如：「已收到，開始處理」)。
6.  **完成設定**：
    - 點擊上方捷徑名稱，重新命名為「**記筆記**」或你喜歡的 Siri 喚醒詞。
    - 點擊「完成」。

> [!IMPORTANT]
> **重要設定**
> 1. 請使用 `/api/v1/note/ios` 端點
> 2. **必須**在 Headers 加入 `X-API-Key`，否則會回傳 401 錯誤
> 3. Request Body 設定為「檔案(File)」而非「表單(Form)」


## 3. 使用方式

- **語音喚醒**：對 iPhone 說「嘿 Siri，記筆記」，待 Siri 開始錄音介面時，說出你的想法。
- **手動執行**：將捷徑加到主畫面，點擊圖示即可開始錄音並上傳。

## 4. 測試指令 (cURL)

### 測試 iOS 專用端點

```bash
curl -X POST "http://localhost:8000/api/v1/note/ios" \
  -H "X-API-Key: your-api-key-here" \
  --data-binary @test_audio.m4a
```

### 測試標準端點（無需 API Key）

```bash
curl -X POST "http://localhost:8000/api/v1/note" \
  -H "Content-Type: multipart/form-data" \
  -F "audio=@test_audio.m4a"
```

## 5. 故障排除

### 錯誤 401: 未授權存取

**原因**：未提供 API Key 或 Key 錯誤

**解決方案**：
1. 確認 Shortcuts 的 Headers 有設定 `X-API-Key`
2. 檢查 Key 值是否與 `.env` 檔案中的 `SIRI_API_KEY` 一致
3. 確認沒有多餘的空格或換行

### 錯誤 413: 檔案過大

**原因**：音訊檔案超過 25MB

**解決方案**：
1. 縮短錄音時間（建議 20 分鐘內）
2. 調整 Shortcuts 錄音品質為「正常」

### 錯誤 400: 不支援的格式

**原因**：檔案格式驗證失敗

**解決方案**：
1. 確認錄製的是音訊而非影片
2. 檢查 Shortcuts 是否正確選擇「錄製音訊」而非「錄製影片」
3. 參考 [AUDIO_FORMAT_VALIDATION.md](./AUDIO_FORMAT_VALIDATION.md) 了解支援格式
