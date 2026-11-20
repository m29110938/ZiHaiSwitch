# 使用到的技術

## 整體架構

```
CGEventTap（listen-only，獨立執行緒）
   └─ 只取 keycode → 分類（一般鍵／空白／Enter／Backspace／修飾鍵）
        └─ 查每鍵自訂 → 選樣本 → 音高 ±3%、音量 0.85～1.0 隨機（np.interp 重取樣）
             └─ deque 交給音訊 callback
                  └─ sounddevice OutputStream（48kHz、128 frames）多聲部混音 → tanh 軟削波 → 耳機
```

狀態列選單（rumps）與各設定視窗（PyObjC / AppKit）跑在主執行緒；按鍵監聽與音訊各自獨立，選單操作不會卡住聲音。

## 技術棧

| 類別 | 使用 | 用途 |
|---|---|---|
| 語言 | Python 3 | 全部程式 |
| 狀態列 | rumps | 狀態列圖示與選單 |
| macOS 介面 | PyObjC（AppKit） | 自訂按鍵、匯入音效、從錄音建立音效視窗、音量滑桿、提示框 |
| 全域按鍵 | Quartz `CGEventTap` | listen-only 監聽 KeyDown／KeyUp／FlagsChanged |
| 安全輸入 | Carbon `IsSecureEventInputEnabled`（ctypes） | 密碼框時整個停止監聽 |
| 音訊輸出 | sounddevice（PortAudio → CoreAudio） | 低延遲串流與自製混音 |
| 訊號處理 | numpy | 重取樣、混音、頻域高通濾波、切片、頻譜重心 |
| 轉檔 | macOS 內建 `afconvert` | 匯入 wav／mp3／m4a／aiff／flac，免額外套件 |
| HTTPS | urllib ＋ certifi | 檢查更新（只連 GitHub） |
| 簽章 | 自寫 Ed25519（RFC 8032） | 驗證更新檔，免第三方加密套件 |
| 打包 | PyInstaller | 產生 .app，排除 scipy 等大型套件 |

## 低延遲音訊

- 取樣率 48kHz（CoreAudio 原生，避免系統重取樣），blocksize 128（約 2.7ms），`latency="low"`，實測輸出延遲約 5.4ms
- 音訊 callback 內不做配置、鎖、I/O；新聲音一律經 `deque` 傳入
- 音高抖動在按鍵執行緒先重取樣（約 30µs），callback 只做加總
- 最多 24 個聲部同時發聲，快速連打不互相截斷；以 `tanh` 軟削波避免疊加爆音
- 所有音效啟動時就載入並快取，按鍵時零合成成本

## 音效來源與切片

- 內建 5 組 CC0 真實錄音（茶軸、青軸、紅軸、黑軸、打字機），來源記錄在 SOUND_SOURCES.md
- 切片演算法（`sampler.py`）：
  - 150Hz 二階 Butterworth 高通（頻域、零相位實作，只用 numpy）→ 每 1ms 的 RMS 能量包絡
  - 包絡陡升 ≥12dB 視為一聲敲擊；只保留尾音在下一聲前收掉、間隔 ≥35ms 的乾淨片段
  - 響度中位數以上為「按下」、以下為「放開」
  - 大鍵（空白、Enter、Backspace）取頻譜重心最低、聲音最長的按下聲
- 從錄音建立音效：App 內直接切片，每聲選用途，編輯中即時套用到打字聲；保留 16-bit 原始錄音與選擇，可再編輯

## 隱私設計

- 按鍵回呼只讀 keycode 判斷類型，不轉成字元、不存、不輸出、不寫檔
- 事件監聽一律 listen-only，不攔截、不修改、不注入
- 安全輸入（密碼框、sudo）啟用時停止事件監聽，回呼內也再檢查一次
- 除了檢查更新，沒有任何網路功能與遙測

## 更新與資安

- GitHub Releases 發佈；只連 HTTPS ＋ GitHub 網域（轉址後的最終網址也檢查）
- 更新檔以 Ed25519 簽章，簽章內容綁定「版本｜CPU 架構｜SHA-256」，公鑰內建於 App
- 只接受比目前新的版本（防降版）；解壓後檢查 bundle ID、版本號、`codesign --verify --deep --strict`
- 安裝腳本先備份舊版，新版開不起來就自動還原

## 輕量化

- 執行時只依賴 numpy、sounddevice、rumps、pyobjc、certifi
- 自寫 WAV 讀寫（`wavio.py`）與頻域濾波，打包時排除 scipy
- 閒置 CPU 約 0.1～0.2%，安裝檔約 17.6MB

## 圖示

- PICO-8 風格 16 色有限色盤、手工點陣、最近鄰放大，無漸層與抗鋸齒（`tools/make_icons.py`）
- 狀態列使用 template 圖，自動適應淺色／深色模式
