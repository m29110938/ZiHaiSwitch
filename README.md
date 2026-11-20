# 自嗨軸 ZiHaiSwitch

macOS 狀態列小工具：打字時在耳機裡播放機械鍵盤音效。無聲鍵盤也能有青軸的爽感。

- 5 種真實錄音音效：茶軸、青軸、紅軸、黑軸、打字機
- 按下到出聲約 5ms，按下、放開各有一聲
- 可匯入自己的音效（wav / mp3 / m4a / aiff / flac），每個按鍵都能個別設定
- 閒置 CPU 約 0.1～0.2%，安裝檔約 17MB
- 內建檢查更新，新版本經簽章驗證後一鍵安裝

## 下載安裝

1. 到 [Releases](../../releases) 下載最新的 `ZiHaiSwitch-x.y.z-macos-arm64.zip`（Apple Silicon，M1 以後的 Mac）
2. 解壓縮，把 `ZiHaiSwitch.app` 拖到「應用程式」資料夾
3. 第一次開啟：因為沒有 Apple 開發者簽章，macOS 會擋下來
   - 在 App 上按右鍵 → 打開 → 打開
   - 或到「系統設定 → 隱私權與安全性」，在下方按「強制打開」
4. 依提示到「系統設定 → 隱私權與安全性 → 輸入監控」開啟 ZiHaiSwitch，再重新開啟 App
5. 狀態列出現 8-bit 喇叭圖示就完成了

需求：macOS 12 以上、Apple Silicon。

## 隱私與安全

- **不記錄任何按鍵內容**：只判斷按鍵類型（一般鍵、空白鍵、Enter…）來決定播哪個聲音，不轉成文字、不存檔、不輸出
- **密碼框自動停止**：系統進入「安全輸入」（輸入密碼、sudo）時，整個停止監聽
- **只監聽、不攔截**：使用 listen-only 事件監聽，不會修改或注入任何按鍵
- **不上傳任何資料**：唯一的網路連線是檢查更新（GitHub Releases），每天最多一次，可在選單關閉「自動檢查更新」
- **更新檔簽章驗證**：每個版本都以發版者的 Ed25519 私鑰簽章，App 內建公鑰驗證；檔案被竄改、來源不是 GitHub、或版本比目前舊，一律拒絕安裝

> 每次更新後，macOS 可能會要求重新開啟「輸入監控」權限（因為沒有 Apple 開發者簽章）。

## 從原始碼執行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

## 發版（維護者）

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python tools/release.py keygen   # 只有第一次：產生簽章金鑰
.venv/bin/python tools/release.py build    # 打包＋簽章，輸出到 dist/
```

1. 改 `version.py` 的 `VERSION`，並在 `CHANGELOG.md` 加上該版本的段落
2. 執行 `build`，到 GitHub 新增 Release，tag 為 `v版本號`
3. 上傳 `dist/` 裡的 `.zip` 與 `.zip.sig`，說明貼 `dist/release-notes.md`

私鑰在 `~/.zihaiswitch/update_signing.key`，**不要放進 git 或分享**。私鑰遺失的話，已安裝的使用者就無法自動更新。

## 音效來源

全部是 CC0 授權的錄音，來源見 [SOUND_SOURCES.md](SOUND_SOURCES.md)。
