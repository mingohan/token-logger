# Token Usage Logger

每小時自動抓取 Claude.ai 和 Ollama Cloud 的使用量，寫入 Google Sheets。

## 架構

```
GitHub Actions (cron 每小時)
  └─ scraper.py
       ├─ claude.ai 內部 JSON API  (session cookie)
       ├─ ollama.com 內部 JSON API (session cookie)
       ├─ 寫入 Google Sheets       (Service Account)
       └─ 滾動更新 cookie → GitHub Secrets (PAT)
```

## 一次性設定步驟

### 1. 取得 Session Cookie

**Claude.ai**
1. 瀏覽器開啟 https://claude.ai/settings/usage（確認已登入）
2. 開啟 DevTools → Application → Cookies → `claude.ai`
3. 複製所有 cookie，格式：`sessionKey=sk-ant-sid-...; _cfuvid=...`

**Ollama**
1. 瀏覽器開啟 https://ollama.com/settings（確認已登入）
2. 同上操作複製所有 cookie

### 2. Google Sheets Service Account

1. 前往 [Google Cloud Console](https://console.cloud.google.com/)
2. 建立專案 → 啟用 **Google Sheets API**
3. IAM & Admin → Service Accounts → 建立帳號
4. 點進帳號 → Keys → Add Key → JSON → 下載
5. 在你的 Google Sheet → Share → 貼上 Service Account email（給 Editor 權限）
6. 從 Sheet URL 複製 Spreadsheet ID（`/d/` 和 `/edit` 之間的字串）

### 3. GitHub Secrets 設定

在 GitHub repo → Settings → Secrets and variables → Actions，新增以下 Secrets：

| Secret 名稱 | 內容 |
|-------------|------|
| `CLAUDE_COOKIE` | 步驟 1 複製的 Claude cookie 字串 |
| `OLLAMA_COOKIE` | 步驟 1 複製的 Ollama cookie 字串 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | 步驟 2 下載的 JSON 檔案**全文** |
| `SPREADSHEET_ID` | 步驟 2 複製的 Sheet ID |
| `GH_PAT` | GitHub Personal Access Token（需有 `secrets:write` 權限） |

> `GH_PAT` 用於 cookie 自動滾動更新，若不需要可留空（cookie 過期時需手動更新）。

### 4. 建立 Google Sheet

在試算表中建立名為 `usage` 的工作表（或自訂名稱後在 Actions Variables 設定 `SHEET_NAME`）。

### 5. 推送到 GitHub

```bash
git init
git remote add origin https://github.com/<你的帳號>/token-logger.git
git add .
git commit -m "init"
git push -u origin main
```

推送後 Actions 會在整點自動執行。可前往 Actions 頁面手動觸發測試。

## Google Sheet 欄位說明

| 欄位 | 說明 |
|------|------|
| timestamp | UTC 時間 |
| claude_session_pct | Claude 當前 session 使用 % |
| claude_weekly_pct | Claude 本週全模型使用 % |
| claude_extra_spent_usd | Claude 本月 extra usage 花費（美元） |
| ollama_session_pct | Ollama 當前 session 使用 % |
| ollama_weekly_pct | Ollama 本週使用 % |

## Cookie 過期處理

- Cookie 自動滾動：每次執行後腳本會更新 Secrets（需設定 `GH_PAT`）
- 若 cookie 失效，Actions run 會標記為 **Failed**，收到 GitHub email 通知後手動更新 Secret 即可
- 預計需要手動更新頻率：每數月一次

## 本機測試

```bash
pip install -r requirements.txt

export CLAUDE_COOKIE="..."
export OLLAMA_COOKIE="..."
export GOOGLE_SERVICE_ACCOUNT_JSON="$(cat service-account.json)"
export SPREADSHEET_ID="..."

python scraper.py
```

## 注意事項

- 建議使用 **private repo** 存放此專案（含 cookie 相關設定）
- Ollama 無官方 usage API，腳本會嘗試數個內部 endpoint；若 Ollama 改版可能需要調整
