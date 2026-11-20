# 自嗨軸 ZiHaiSwitch

macOS 狀態列常駐的「無聲機械鍵盤音效模擬器」：全域監聽 KeyDown，在耳機即時播放程序化合成的軸體音效。
目標：**按鍵到出聲 < 20ms、閒置 CPU ≈ 0、絕不碰按鍵內容**。

## 目錄結構

```
ZiHaiSwitch/
├── CLAUDE.md
├── requirements.txt
├── main.py             # 音訊引擎、按鍵分類、CGEventTap、安全輸入偵測、rumps 選單
├── sound_generator.py  # 軸體註冊表（PROFILES / get_bank）；程序化合成器已停用，只留 --export 試聽
├── library.py          # 設定檔讀寫、音效匯入（afconvert）、內建＋匯入音效庫
├── settings_window.py  # 「自訂按鍵」視窗：最上方「全部按鍵」一鍵切換整組音效（會確認並清除個別設定），再點鍵個別設定；試打區
├── import_window.py    # 「匯入音效」視窗：匯入後直接在列表名稱欄命名（不跳對話框）、試聽、刪除
├── record_window.py    # 「從錄音建立音效」視窗：匯入整段打字錄音→自動切片→勾選／指定用途→存成一組音效
├── ui.py               # 共用提示框 ui.alert()：一律帶程式圖示（原始碼執行時系統預設是 Python 圖示），不要直接 NSAlert
├── keymap.py           # 設定視窗用的 Mac ANSI 鍵位表（只供顯示）
├── sampler.py          # 取樣軸體：錄音偵測敲擊、切片、分按下／放開，存成 profiles/<name>/（只用 numpy，App 內可用）
├── version.py          # VERSION、BUNDLE_ID、UPDATE_REPO（GitHub 帳號/repo）、更新簽章公鑰
├── updater.py          # 檢查更新／下載／驗證／替換安裝（GitHub Releases）
├── ed25519.py          # 純 Python Ed25519（RFC 8032），驗證更新簽章
├── soundbank.py        # 音效註冊表（執行時用，只依賴 numpy）
├── wavio.py            # WAV 讀寫（取代 scipy.io.wavfile）
├── paths.py            # 資源路徑（開發 vs 打包 .app）
├── docs/TECH.md        # 使用到的技術（給人看的說明，也會同步進 Obsidian 筆記）；改架構／技術時要一起更新
├── tools/obsidian_sync.py # 產生 Obsidian 筆記「自嗨軸 ZiHaiSwitch.md」（README 功能＋TECH.md＋模組說明＋相依＋CHANGELOG），release.py build 後自動執行
├── tools/release.py    # 發版：keygen（產生簽章金鑰）、build（PyInstaller 打包＋zip＋簽章）
├── CHANGELOG.md / README.md
├── tools/make_icons.py # 產生 8-bit 圖示（狀態列 template + AppIcon.icns）
├── tools/slice_recording.py # 錄音切片 CLI（--typing 整段打字 / --down --up 單鍵 / --bell 鈴聲）
├── assets/profiles/    # 隨 App 發佈的真實錄音軸體（茶/青/紅/黑/打字機），由 slice_recording.py 產生
├── sound/              # 下載的原始錄音（不進版控，來源與授權見 SOUND_SOURCES.md）
└── assets/
    ├── icons/          # 由 make_icons.py 產生，進版控
    └── *.wav           # `python sound_generator.py --export` 試聽檔（不進版控）

使用者資料：`~/Library/Application Support/ZiHaiSwitch/`
- `config.json`：音量 0～100、音效（軸體）、靜音、打字機鈴聲、每鍵自訂 `keys: {"<keycode>": {"down": id, "up": id}}`
- `sounds/names.json`：匯入音效的顯示名稱
- `sounds/*.wav`：匯入後的音效（48kHz mono float32、已裁掉開頭靜音、峰值 0.6、最長 2 秒）
- 音效 ID：`default`（跟隨軸體）、`none`（無聲）、`builtin:<profile>:<kind>`、`user:<檔名>`
```

## 執行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py              # 狀態列 App
.venv/bin/python main.py --selftest   # 不需權限：播放全部音效、印出延遲參數
.venv/bin/python sound_generator.py --export   # 輸出 wav 到 assets/
```

## macOS 權限（Input Monitoring / Accessibility）

- 使用 `kCGEventTapOptionListenOnly` 的 CGEventTap，需要 **「輸入監控（Input Monitoring）」**。
  程式啟動時會呼叫 `CGRequestListenEventAccess()` 讓系統跳授權視窗。
- 權限授予的是**啟動 Python 的那個 App**（Terminal、iTerm、VS Code、Claude 桌面版…），不是 main.py。
- 路徑：系統設定 → 隱私權與安全性 → 輸入監控 → 開啟對應 App；若仍抓不到，再開「輔助使用（Accessibility）」。
- 授權後必須**完全結束並重開**該 App，權限才會生效。
- `CGEventTapCreate` 回傳 `None` ＝ 沒權限，程式會在終端機印出引導文字，選單仍可開啟但不會出聲。
- 之後若打包成 `.app`（py2app），權限要改授給該 `.app`，且需設 `LSUIElement` 隱藏 Dock 圖示。

## 低延遲音訊規範

| 參數 | 值 | 原因 |
|---|---|---|
| 引擎 | `sounddevice.OutputStream` + 自製混音 callback | 不用高階播放器；可完全掌控 buffer |
| samplerate | 48000 | macOS CoreAudio 原生，避免系統重取樣 |
| blocksize | 128（≈2.7ms） | 實測 stream latency 約 5.4ms |
| latency | `"low"` | |
| 聲部 | `MAX_VOICES = 24`，tanh 軟削波 | 快速連打不互相截斷、疊加不爆音 |

規則：
- **audio callback 內禁止**：配置大陣列以外的重工作、鎖、I/O、print。新聲音一律經 `deque` 交給 callback。
- 音高偏移（jitter）在**按鍵執行緒**做重取樣（`np.interp`，約 30µs），不在 audio callback 做。
- 所有音效在啟動時合成並快取（`sound_generator.get_bank` 有 `lru_cache`），按鍵時零合成成本。
- 藍牙耳機本身有 100～200ms 傳輸延遲，無法由程式解決；延遲驗收請用有線耳機或內建喇叭。
- 改動 blocksize 後要跑 `--selftest` 確認 latency 與爆音狀況。

## 隱私邊界（鐵則，任何修改都不得違反）

1. 按鍵回呼**只讀 keycode 判斷類型與查詢自訂音效**（NORMAL / SPACE / ENTER / BACKSPACE / MODIFIER），
   不得呼叫 `CGEventKeyboardGetUnicodeString`、不得把 keycode 轉字元。`keymap.py` 的鍵名只給設定視窗顯示用。
   config.json 只存「哪個鍵配哪個音效」的設定，絕不存任何打字歷程。
2. **不得儲存、print、log、寫檔**任何 keycode 或按鍵內容；
3. 回呼內 `except: pass`，例外訊息也不得帶出事件內容。
4. Event tap 一律 **listen-only**，不攔截、不修改、不注入事件。
5. **安全輸入（Secure Event Input）**：`IsSecureEventInputEnabled()` 為 True 時（密碼框、sudo），
   - rumps Timer 每 0.5 秒輪詢，切換時 `CGEventTapEnable(tap, False)` 整個停掉監聽；
   - 回呼內也再檢查一次，雙重保險；
   - 狀態列圖示切成 🔒。
6. 不得加入任何網路功能、遙測、上傳。

## 功能規格摘要

- 軸體：茶軸 `brown`、青軸 `blue`、打字機 `typewriter`（Enter 附「叮」鈴聲）。
- 合成方式：極短噪音／脈衝激發 → 高阻尼二階共振器組（模態合成），外加外殼殘響；**不要用純正弦當主體**（聽起來像電子音、不像塑膠撞擊）。
- 每次按鍵有「按下（觸底）」與「放開（回彈，`<kind>_up`，音量約 0.38 倍）」兩聲；KeyUp 只為有收到 KeyDown 的鍵發聲。
- Space / Enter / Backspace 有獨立、較低沉的音效；修飾鍵按下、放開各一聲；長按 autorepeat 不發聲。
- 調音色時用功率加權頻譜重心當參考：茶軸約 1.2kHz、青軸約 2.3kHz（click 能量在 2～4kHz）、打字機約 1.9kHz。
- 每次按鍵 Pitch Jitter ±3%、音量 0.85～1.0 隨機。
- 選單：第一行程式名稱「自嗨軸 ZiHaiSwitch」、音效開啟、音量滑桿 0～100%（平方曲線換算增益）、音效（切換軸體）、打字機 Enter 鈴聲、匯入音效…、從錄音建立音效…、自訂按鍵…、檢查更新…、自動檢查更新、退出。所有設定自動存檔。心流 Combo 已移除（使用者要求）。
- 不在 Dock 顯示：`setActivationPolicy_(Accessory)`；打包 .app 時改用 Info.plist `LSUIElement`。
- 狀態列圖示：8-bit 像素喇叭 template 圖（on 聲波／muted ✕／secure 鎖），音量 0% 也顯示 muted。
- 圖示風格：PICO-8 式 16 色有限色盤、硬邊點陣、最近鄰放大；不要漸層、光暈、抗鋸齒。改圖示請改 `tools/make_icons.py` 的點陣再重新產生。
- **App 只使用真實錄音的軸體，不使用程序化合成音效**（使用者要求）。
- 取樣軸體：內建在 `assets/profiles/<name>/`，使用者自切的在 `~/Library/Application Support/ZiHaiSwitch/profiles/<name>/`（`--user`），啟動時 `sampler.load_profiles()` 註冊進 `sg.PROFILES`（ID `rec:<name>`）。
  切片規則：能量包絡陡升 ≥12dB 為一聲；只用尾音在下一聲前收掉、間隔 ≥35ms 的乾淨片段；響度中位數以上為按下、以下為放開；
  大鍵（Space/Enter/Backspace）取頻譜最低沉、最長的按下聲。新增取樣音效只能用 CC0 或可商用授權，並記錄到 `SOUND_SOURCES.md`。
- 匯入音效同時註冊成一種「音效」（ID `user:<檔名>`）：選了之後所有鍵按下都播它、放開無聲；出現在選單「音效」分隔線下方。
- 匯入音效：選單「匯入音效…」，用系統 `afconvert` 轉檔（wav/aiff/mp3/m4a/caf/flac）。
- 從錄音建立音效：`sampler.analyze()` 切出乾淨敲擊（最多 120 秒、300 聲）並預選（同 bank_from_typing 的挑法），每一聲只有「用途」一欄（不使用／一般鍵按下／放開、空白、Enter、Backspace、修飾鍵，不要再加勾選欄：使用者看不懂），改用途時自動試聽；匯入或修改後 `player.trial` 立即改成編輯中的音效（所有鍵、忽略每鍵自訂），關閉視窗／建立／切換時還原；`bank_from_selection()` 缺的用途以一般鍵代替、沒選放開聲就用按下聲 ×0.38；存到使用者 profiles/`my_<時間戳>`，建立後自動切換過去。
  另存 `edit/source.wav`（16-bit，只到最後一聲＋0.5 秒）與 `edit/selection.json`，視窗上方下拉「我的音效」可重新編輯（儲存時沿用同一 ID）、改名、刪除；沒有 `edit/` 的舊資料只能改名、刪除。`load_profiles` 只讀資料夾第一層 wav，不會載入 edit/。
- 刪除音效（自建或匯入）一律先跳確認；`library.forget_sound()` 會把設定中引用它的目前音效、每鍵自訂清掉（目前音效改回 rec:brown）。

## 新增軸體

在 `sound_generator.py` 寫 `synth_xxx(rng, pitch, decay, gain, rattle)`，加入 `SYNTHS` 與 `PROFILES`，選單會自動出現。

## 版本、打包與更新

- 發新版：改 `version.py` 的 `VERSION` → `CHANGELOG.md` 加 `## vX.Y.Z` 段落 → `tools/release.py build` → GitHub Release（tag `vX.Y.Z`）上傳 `.zip` 與 `.zip.sig`。
- **輕量化規則**：執行時只能依賴 numpy / sounddevice / rumps / pyobjc / certifi；scipy 只給程序化合成工具用（切片的高通濾波已改純 numpy 頻域實作），打包時 `--exclude-module scipy`。新增執行時依賴前要先確認體積。
- **更新資安規則（不得放寬）**：只連 HTTPS＋GitHub 網域（含轉址最終網址）；Ed25519 簽章綁定「版本｜架構｜SHA-256」；只接受較新版本；解壓後檢查 bundle ID、版本、`codesign --verify --deep --strict`；替換失敗自動還原；請求不帶使用者資料。
- Obsidian 筆記資料夾設定在 `~/.zihaiswitch/obsidian.json`（不進 git，repo 是公開的，不要把 vault 路徑寫進程式）；每次發版整篇覆蓋，模組第一行 docstring 會出現在筆記的「程式結構」表，要保持正確。
- 簽章私鑰：`~/.zihaiswitch/update_signing.key`（權限 600），**絕不進 git**。
- App 為 ad-hoc 簽章（無 Apple Developer ID）：使用者第一次要右鍵→打開；每次更新後「輸入監控」權限可能要重新開啟。
- 實測（v1.0.0 打包版）：zip 17.5MB、.app 38MB、閒置 CPU 0.1～0.2%、RSS 約 118MB。
