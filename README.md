# IG 雲端自動發布（GitHub Actions）

把 Instagram 的定時發布完全搬到 GitHub 的伺服器上執行。**你的電腦不需要在發布時間開機，也不需要守時。**

```
你的電腦（偶爾）                      GitHub 伺服器（24 小時）
─────────────────                    ─────────────────────────
tools\sync_cloud.py --push    →      .github/workflows/publish.yml
  把待發布的影片與文案推上去            每 15 分鐘執行一次
                                      scripts/publish_due.py
                                        → 檢查有沒有到期的貼文
                                        → 有：呼叫 IG API 發布 Reels
                                        → 把發布狀態 commit 回 repo
```

## 一次性設定（約 5 分鐘）

### 1. 在 GitHub 建立一個 **public** repo

repo 必須是 public，因為 IG 的伺服器要能直接抓取影片檔（私有 repo 的 raw 網址需要驗證）。
到 <https://github.com/new> 建立，例如名稱 `ig-publish`，**不要**勾選 Add README。

### 2. 把內容推上去

```powershell
cd C:\Users\User\Desktop\VIDEO\ig\cloud-repo
git add .
git commit -m "initial: pending posts"
git remote add origin https://github.com/<你的帳號>/ig-publish.git
git push -u origin main
```

> 第一次 push 會要求登入 GitHub（你的電腦已有 github.com 憑證設定）。
> 若要用 token：GitHub → Settings → Developer settings → Personal access tokens，
> 建立有 `repo` 權限的 token，push 時當密碼輸入即可。

### 3. 設定 Secrets（`IG_USER_ID`、`IG_ACCESS_TOKEN`）

repo → **Settings → Secrets and variables → Actions → New repository secret**，新增兩個：

| Name | Value |
|---|---|
| `IG_USER_ID` | `17841455111601960` |
| `IG_ACCESS_TOKEN` | 你 `tools\config.env` 裡的那組長效 token |

> 放 Secrets 不會出現在程式碼或日誌中。**不要**把 token 寫進 repo 檔案。

### 4. 啟動 workflow

`Actions` 分頁 → 若出現提示就按 **I understand my workflows, go ahead and enable them**。
排程會在每小時的 0/15/30/45 分自動執行。

想先測試：`Actions → IG publish → Run workflow`，把 `dry_run` 勾起來，看它列出什麼（不會真的發布）。

### 5. 停用本機排程（避免兩邊都發）

```powershell
schtasks /Change /TN "IG 自動發布" /DISABLE
```

要改回本機發布時：`schtasks /Change /TN "IG 自動發布" /ENABLE`

---

## 之後的日常維護

有新內容時（在 `tools\` 執行）：

```powershell
python sync_cloud.py --dry-run    # 先看會同步什麼
python sync_cloud.py --push       # 同步並推上 GitHub
```

之後就交給雲端。你的電腦可以關機。

---

## 影片網址是怎麼來的（已實測）

IG 發布時只接受「公開可下載的影片網址」，所以 `publish_due.py` 會依序嘗試並自動挑一個可用的：

| 順位 | 來源 | 實測 Content-Type | 備註 |
|---|---|---|---|
| 1 | `VIDEO_BASE_URL`（若有設定） | 依你的設定 | 給 GitHub Pages 或自架空間用 |
| 2 | GitHub Pages `https://<owner>.github.io/<repo>/...` | `video/mp4` | 需在 Settings → Pages 啟用 |
| 3 | **jsDelivr CDN** `https://cdn.jsdelivr.net/gh/<owner>/<repo>@<sha>/...` | **`video/mp4`** ✅ | 預設可用，不需設定 |
| 4 | raw.githubusercontent.com | `application/octet-stream` | 備援 |

腳本會先發 HEAD 檢查，優先選回傳 `video/*` 的來源（已用公開 repo 的 mp4 實測 jsDelivr 回傳 `video/mp4`）。
若 jsDelivr 尚未索引到新 commit，會自動退回 raw 網址。

---

## 注意事項

- **GitHub Actions 的排程可能延遲 5–20 分鐘**（尖峰時段）。因為是「檢查有沒有到期」而非「準點發」，延遲只會讓貼文晚幾分鐘，不會漏發。
- 若 repo 連續 60 天沒有任何活動，GitHub 會停用排程；本流程每次發布都會 commit 狀態，所以正常運作下不會發生。
- **IG token 約 2026-12-12 到期**。到期前請刷新 token 並更新 GitHub Secret：
  ```powershell
  python C:\Users\User\Desktop\VIDEO\ig\tools\ig_autopost.py refresh-token
  ```
  然後到 repo → Settings → Secrets → 更新 `IG_ACCESS_TOKEN`。
- 每日上限 6 篇、每次最多 2 篇（可在 workflow 的 `MAX_POSTS` / `DAILY_CAP` 調整）。
- 本機的 `success/` 仍是內容的唯一來源；`cloud-repo/posts/` 是它的發布副本。

## 目錄

```
cloud-repo/
├─ .github/workflows/publish.yml   GitHub Actions 排程與執行步驟
├─ scripts/publish_due.py          實際發布邏輯（只用 Python 標準庫）
├─ posts/<slug>/post.json          標題、文案、排定時間、狀態
├─ posts/<slug>/video.mp4          要發布的影片（_reels 正規化版優先）
└─ README.md                       本文件
```
