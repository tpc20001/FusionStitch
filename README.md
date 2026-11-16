# FusionStitch

GoPro Fusion 360 素材 → 拼接、調色、轉檔成可以上 YouTube 的 360 影片（Windows，NVIDIA GPU）。

## 發新版
1. 改 `app/core.py` 的 `APP_VERSION`，在 `app/CHANGELOG.json` 最上面加一筆（version、date、title、items）。
2. 推到 `main` → GitHub Actions 自動打包、自我測試，放到 Releases（標籤 `v<版本>`；同一版發過就不重發）。
3. 程式開著的電腦：1 分鐘～6 小時內會提示有新版（或在「線上更新」／手機「版本與更新」按 ☁ 檢查更新），按「更新」才會下載。

要換 EXE（加新套件、改啟動器）：再把 `build/launcher.py` 的 `LAUNCHER_VERSION` 加 1。

## Release 檔案
| 檔案 | 用途 |
|---|---|
| `FS_V<版本>_Update.zip` | 程式更新包（熱更新，轉檔不中斷） |
| `FS_Shell_L<n>.zip` | EXE 外殼（FusionStitch.exe＋_internal），啟動器版本變大才用 |
| `FusionStitch_Full_V<版本>.zip` | 第一次安裝（不含 FFmpeg：放到 `tools\ffmpeg\`） |
| `update.json` | 版本、啟動器版本、下載網址、SHA-256、更新說明 |

## 資料夾
```
FusionStitch\FusionStitch.exe, _internal\   啟動器（雲端打包）
FusionStitch\app\                          程式（更新只換這裡）
FusionStitch\UPDATE\                       放更新包，下次開程式套用
FusionStitch\Package\  LOG\  app_backup\   設定／佇列、紀錄、更新前備份
FusionStitch\tools\ffmpeg\                 ffmpeg.exe、ffprobe.exe（NVENC）
```
