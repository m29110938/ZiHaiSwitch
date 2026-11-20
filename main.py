"""自嗨軸 ZiHaiSwitch — macOS 無聲機械鍵盤音效模擬器。

執行：
    .venv/bin/python main.py             # 狀態列 App
    .venv/bin/python main.py --selftest  # 不需權限：自動播放全部音效並回報延遲參數

隱私邊界：按鍵回呼只讀 keycode 判斷「類型」與查詢自訂音效，不轉字元、不儲存、不 log。
"""

from __future__ import annotations

import argparse
import collections
import ctypes
import queue
import random
import signal
import threading
import time
from enum import Enum
from pathlib import Path

import numpy as np
import sounddevice as sd

import library
import updater
import version as V
from paths import ASSETS, app_bundle
import sampler
import soundbank as sg

# ====================================================================== 音訊引擎
BLOCKSIZE = 128          # 128 / 48000 ≈ 2.7ms 一個 callback
MAX_VOICES = 24          # 同時發聲上限，超過丟最舊的


def volume_to_gain(percent: int) -> float:
    """0～100% → 線性增益。平方曲線讓滑桿的聽感比較均勻。"""
    p = min(100, max(0, percent)) / 100.0
    return p * p


class AudioEngine:
    """sounddevice 低延遲輸出 + 自製多聲部混音器（polyphony，不互相截斷）。"""

    def __init__(self, samplerate: int = sg.SAMPLE_RATE, blocksize: int = BLOCKSIZE):
        self._pending: collections.deque = collections.deque()  # 執行緒安全的 append/popleft
        self._voices: list[list] = []                            # [sample, pos, gain]
        self.master = volume_to_gain(65)
        self.stream = sd.OutputStream(
            samplerate=samplerate,
            blocksize=blocksize,
            channels=2,
            dtype="float32",
            latency="low",
            callback=self._callback,
        )

    def start(self) -> None:
        self.stream.start()

    def stop(self) -> None:
        self.stream.stop()
        self.stream.close()

    @property
    def output_latency_ms(self) -> float:
        return self.stream.latency * 1000

    def play(self, sample: np.ndarray, gain: float = 1.0, pitch: float = 1.0) -> None:
        if pitch != 1.0:
            # 線性內插重取樣：pitch>1 變高變短。短樣本（數千點）約數十 µs
            n = int(len(sample) / pitch)
            sample = np.interp(np.arange(n) * pitch, np.arange(len(sample)), sample).astype(np.float32)
        self._pending.append([sample, 0, gain])

    def _callback(self, outdata, frames, time_info, status) -> None:
        while self._pending:
            self._voices.append(self._pending.popleft())
        if len(self._voices) > MAX_VOICES:
            del self._voices[: len(self._voices) - MAX_VOICES]
        if not self._voices:
            outdata.fill(0)
            return
        mix = np.zeros(frames, dtype=np.float32)
        alive = []
        for v in self._voices:
            sample, pos, gain = v
            chunk = sample[pos : pos + frames]
            mix[: len(chunk)] += chunk * gain
            v[1] = pos + frames
            if v[1] < len(sample):
                alive.append(v)
        self._voices = alive
        mix *= self.master
        np.tanh(mix, out=mix)  # 軟削波，連打疊加也不爆音
        outdata[:, 0] = mix
        outdata[:, 1] = mix


# ====================================================================== 按鍵分類
class KeyKind(Enum):
    NORMAL = "normal"
    SPACE = "space"
    ENTER = "enter"
    BACKSPACE = "backspace"
    MODIFIER = "modifier"


# macOS 虛擬鍵碼（只用來分類，不轉成字元）
_KC_ENTER = {36, 76}          # Return、Keypad Enter
_KC_SPACE = {49}
_KC_BACKSPACE = {51, 117}     # Delete、Forward Delete
_KC_MODIFIER = {54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 179}  # cmd/shift/caps/opt/ctrl/fn/globe


def classify(keycode: int) -> KeyKind:
    if keycode in _KC_ENTER:
        return KeyKind.ENTER
    if keycode in _KC_SPACE:
        return KeyKind.SPACE
    if keycode in _KC_BACKSPACE:
        return KeyKind.BACKSPACE
    if keycode in _KC_MODIFIER:
        return KeyKind.MODIFIER
    return KeyKind.NORMAL


# ====================================================================== 播放控制
class SwitchPlayer:
    def __init__(self, engine: AudioEngine, cfg: dict, lib: library.SoundLibrary):
        self.engine = engine
        self.cfg = cfg
        self.lib = lib
        self.trial = None  # 「從錄音建立音效」編輯中的音效：設定時所有鍵暫時改播它
        for p in sg.PROFILES:  # 啟動時預先合成並快取全部軸體
            sg.get_bank(p)
        self.engine.master = volume_to_gain(cfg["volume"])

    def _default_samples(self, kind: KeyKind, up: bool) -> list[np.ndarray]:
        bank = sg.get_bank(self.cfg["profile"])
        name = kind.value
        if name == "enter" and self.cfg["bell"] and "enter_bell" in bank:
            name = "enter_bell"
        return bank[f"{name}_up" if up else name]

    def _samples(self, kind: KeyKind, up: bool, keycode: int | None):
        """回傳要播放的樣本變體；None 表示這個鍵被設成無聲。"""
        trial = self.trial
        if trial is not None:
            return trial.get(f"{kind.value}_up" if up else kind.value)
        if keycode is not None:
            override = self.cfg["keys"].get(str(keycode))
            if override:
                sid = override.get("up" if up else "down", "default")
                if sid == "none":
                    return None
                if sid != "default":
                    found = self.lib.samples(sid, up)
                    if found:
                        return found
        return self._default_samples(kind, up)

    def trigger(self, kind: KeyKind, up: bool = False, keycode: int | None = None) -> None:
        if self.cfg["muted"]:
            return
        samples = self._samples(kind, up, keycode)
        if not samples:
            return
        jitter = random.uniform(0.97, 1.03)   # Pitch Jitter ±3%
        gain = random.uniform(0.85, 1.0)      # 音量微調
        self.engine.play(random.choice(samples), gain=gain, pitch=jitter)

    def preview(self, keycode: int) -> None:
        """設定視窗試聽：按下 → 90ms 後放開（不受靜音影響）。"""
        kind = classify(keycode)
        for up, delay in ((False, 0.0), (True, 0.09)):
            samples = self._samples(kind, up, keycode)
            if samples:
                s = random.choice(samples)
                threading.Timer(delay, self.engine.play, args=(s,)).start()


# ====================================================================== 安全輸入偵測
_carbon = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Carbon.framework/Carbon")
_carbon.IsSecureEventInputEnabled.restype = ctypes.c_bool


def secure_input_enabled() -> bool:
    """密碼框、sudo 等啟用 Secure Event Input 時為 True。"""
    return bool(_carbon.IsSecureEventInputEnabled())


# ====================================================================== 全域按鍵監聽
class KeyTap:
    """CGEventTap（listen-only）跑在獨立執行緒的 CFRunLoop，不受選單操作阻塞。"""

    def __init__(self, on_key):
        self.on_key = on_key  # on_key(kind, up, keycode)
        self.tap = None
        self.paused = False
        self._loop = None
        self._prev_flags = 0
        self._down: set[int] = set()   # 目前按住中的 keycode（只用來配對放開，不保存歷史）
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="KeyTap", daemon=True)

    def start(self) -> bool:
        self._thread.start()
        self._ready.wait(3)
        return self.tap is not None

    def set_paused(self, paused: bool) -> None:
        import Quartz

        self.paused = paused
        self._down.clear()
        if self.tap is not None:
            Quartz.CGEventTapEnable(self.tap, not paused)

    def _callback(self, proxy, etype, event, refcon):
        import Quartz

        if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            if not self.paused:
                Quartz.CGEventTapEnable(self.tap, True)
            return event
        if self.paused or secure_input_enabled():
            return event
        try:
            keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            if etype == Quartz.kCGEventKeyDown:
                # 長按自動重複不發聲（真實鍵盤長按也只有一聲）
                if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat):
                    return event
                self._down.add(keycode)
                self.on_key(classify(keycode), False, keycode)
            elif etype == Quartz.kCGEventKeyUp:
                if keycode in self._down:  # 只為「有聽到按下」的鍵播回彈
                    self._down.discard(keycode)
                    self.on_key(classify(keycode), True, keycode)
            elif etype == Quartz.kCGEventFlagsChanged:
                flags = Quartz.CGEventGetFlags(event) & 0xFFFF0000
                pressed = flags & ~self._prev_flags
                released = self._prev_flags & ~flags
                self._prev_flags = flags
                if pressed:
                    self.on_key(KeyKind.MODIFIER, False, keycode)
                elif released:
                    self.on_key(KeyKind.MODIFIER, True, keycode)
        except Exception:
            pass  # 回呼內絕不拋例外、也不印出事件內容
        return event

    def _run(self) -> None:
        import Quartz

        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
            | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
        )
        self.tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            self._callback,
            None,
        )
        if self.tap is None:
            self._ready.set()
            return
        source = Quartz.CFMachPortCreateRunLoopSource(None, self.tap, 0)
        self._loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._loop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self.tap, True)
        self._ready.set()
        Quartz.CFRunLoopRun()


def request_permission() -> bool:
    """listen-only event tap 需要「輸入監控」權限；沒有就請系統跳出授權。"""
    import Quartz

    if Quartz.CGPreflightListenEventAccess():
        return True
    Quartz.CGRequestListenEventAccess()
    return False


PERMISSION_HINT = """
[ZiHaiSwitch] 無法監聽鍵盤：尚未取得權限。
  請到「系統設定 → 隱私權與安全性」：
    1. 「輸入監控」開啟你執行本程式的 App（Terminal / iTerm / VS Code …）
    2. 若仍無效，「輔助使用」也一併開啟
  開啟後需「完全結束並重開」該終端機 App，再重新執行 main.py。
"""


# ====================================================================== 狀態列 App
def _volume_menu_view(initial: int, on_change):
    """狀態列選單內嵌的 0～100% 音量滑桿。"""
    import objc
    from AppKit import NSFont, NSSlider, NSTextField, NSView
    from Foundation import NSMakeRect, NSObject

    class _VolumeTarget(NSObject):
        def changed_(self, sender):
            v = int(round(sender.doubleValue()))
            self.label.setStringValue_(f"{v}%")
            self.callback(v)

    target = _VolumeTarget.alloc().init()
    view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 250, 30))
    title = NSTextField.labelWithString_("音量")
    title.setFrame_(NSMakeRect(28, 6, 40, 18))
    title.setFont_(NSFont.menuFontOfSize_(0))
    slider = NSSlider.sliderWithValue_minValue_maxValue_target_action_(
        initial, 0, 100, target, objc.selector(target.changed_, signature=b"v@:@"))
    slider.setFrame_(NSMakeRect(68, 4, 136, 22))
    slider.setContinuous_(True)
    pct = NSTextField.labelWithString_(f"{initial}%")
    pct.setFrame_(NSMakeRect(208, 6, 40, 18))
    pct.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(NSFont.menuFontOfSize_(0).pointSize(), 0))
    target.label = pct
    target.callback = on_change
    for v in (title, slider, pct):
        view.addSubview_(v)
    return view, target, slider


ICON_DIR = ASSETS / "icons"


def _status_icon(state: str):
    """8-bit 狀態列圖示：1x + 2x 點陣，template 模式自動適配深淺色選單列。"""
    from AppKit import NSBitmapImageRep, NSImage

    img = NSImage.alloc().initWithSize_((18, 18))
    for name in (f"status_{state}.png", f"status_{state}@2x.png"):
        rep = NSBitmapImageRep.imageRepWithContentsOfFile_(str(ICON_DIR / name))
        if rep is not None:
            rep.setSize_((18, 18))
            img.addRepresentation_(rep)
    img.setTemplate_(True)
    return img


def _style_header(nsitem) -> None:
    """選單第一行：程式名稱（粗體、一般字色、不可點）。用自訂 view 避免停用項目被系統畫成灰字。"""
    from AppKit import NSFont, NSTextField, NSView
    from Foundation import NSMakeRect

    from AppKit import NSColor

    view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 250, 24))
    label = NSTextField.labelWithString_(nsitem.title())
    label.setFont_(NSFont.boldSystemFontOfSize_(NSFont.menuFontOfSize_(0).pointSize()))
    label.setFrame_(NSMakeRect(28, 3, 160, 18))
    ver = NSTextField.labelWithString_(f"v{V.VERSION}")
    ver.setFont_(NSFont.menuFontOfSize_(NSFont.smallSystemFontSize()))
    ver.setTextColor_(NSColor.secondaryLabelColor())
    ver.setAlignment_(2)  # right
    ver.setFrame_(NSMakeRect(170, 4, 66, 16))
    view.addSubview_(label)
    view.addSubview_(ver)
    nsitem.setView_(view)
    nsitem.setEnabled_(False)


def run_app() -> None:
    import rumps
    from AppKit import NSApplication, NSImage

    from import_window import open_import
    from record_window import open_record
    from settings_window import open_settings, refresh_key_window

    # 不在 Dock 顯示（只常駐狀態列）
    NSApplication.sharedApplication().setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory

    sampler.load_profiles()  # 先註冊取樣軸體與匯入音效，設定檔裡選的音效才不會被重設
    lib = library.SoundLibrary()
    cfg = library.load_config()
    engine = AudioEngine()
    engine.start()
    player = SwitchPlayer(engine, cfg, lib)
    tap = KeyTap(player.trigger)

    print(f"[ZiHaiSwitch] 音訊輸出延遲 {engine.output_latency_ms:.1f} ms，"
          f"block {BLOCKSIZE} ({BLOCKSIZE / sg.SAMPLE_RATE * 1000:.1f} ms)")
    granted = request_permission()
    if not tap.start() or not granted:
        print(PERMISSION_HINT)
    else:
        print("[ZiHaiSwitch] 鍵盤監聽中（Ctrl+C 或選單「退出」結束）")

    icons = {state: _status_icon(state) for state in ("on", "muted", "secure")}
    app_icon = NSImage.alloc().initWithContentsOfFile_(str(ICON_DIR / "AppIcon.icns"))
    if app_icon is not None:
        NSApplication.sharedApplication().setApplicationIconImage_(app_icon)

    class ZiHaiApp(rumps.App):
        def __init__(self):
            super().__init__("ZiHaiSwitch", quit_button=None)
            self.secure = False
            self._save_timer = None

            self.mute_item = rumps.MenuItem("音效開啟", callback=self.toggle_mute)
            self.mute_item.state = int(not cfg["muted"])

            self.volume_item = rumps.MenuItem("音量")
            view, self._vol_target, self._vol_slider = _volume_menu_view(cfg["volume"], self.set_volume)
            self.volume_item._menuitem.setView_(view)

            self.header = rumps.MenuItem(V.APP_NAME)
            _style_header(self.header._menuitem)

            self.profile_menu = rumps.MenuItem("音效")
            self.build_profile_menu()

            self.bell_item = rumps.MenuItem("打字機 Enter 鈴聲", callback=self.toggle_bell)
            self.bell_item.state = int(cfg["bell"])

            self.update_item = rumps.MenuItem("檢查更新…", callback=self.update_clicked)
            self.auto_update_item = rumps.MenuItem("自動檢查更新", callback=self.toggle_auto_update)
            self.auto_update_item.state = int(cfg["auto_update"])
            self._ui_queue = queue.Queue()
            self._latest = None      # 已知的新版本 Release
            self._busy = False

            self.menu = [
                self.header,
                None,
                self.mute_item,
                self.volume_item,
                None,
                self.profile_menu,
                self.bell_item,
                rumps.MenuItem("匯入音效…", callback=self.open_import),
                rumps.MenuItem("從錄音建立音效…", callback=self.open_record),
                rumps.MenuItem("自訂按鍵…", callback=self.open_keys),
                None,
                self.update_item,
                self.auto_update_item,
                None,
                rumps.MenuItem("退出", callback=self.quit),
            ]
            self.refresh_title()
            self.timer = rumps.Timer(self.poll_secure_input, 0.5)
            self.timer.start()

        def save(self):
            library.save_config(cfg)

        def save_later(self):
            # 拖動滑桿時合併寫檔
            if self._save_timer:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(0.5, self.save)
            self._save_timer.start()

        def refresh_title(self):
            if self.secure:
                state = "secure"
            elif cfg["muted"] or cfg["volume"] == 0:
                state = "muted"
            else:
                state = "on"
            self._icon_nsimage = icons[state]
            nsapp = getattr(self, "_nsapp", None)
            if nsapp is not None:
                nsapp.setStatusBarIcon()

        def toggle_mute(self, item):
            cfg["muted"] = not cfg["muted"]
            item.state = int(not cfg["muted"])
            self.refresh_title()
            self.save()

        def set_volume(self, value: int):
            cfg["volume"] = value
            engine.master = volume_to_gain(value)
            self.refresh_title()
            self.save_later()

        def build_profile_menu(self):
            """內建音效 + 匯入音效（分隔線隔開）。匯入或改名後重建。"""
            if len(self.profile_menu):
                self.profile_menu.clear()
            builtin = [(k, v) for k, v in sg.PROFILES.items() if not k.startswith("user:")]
            user = [(k, v) for k, v in sg.PROFILES.items() if k.startswith("user:")]
            used = set()
            for group_i, group in enumerate((builtin, user)):
                if group_i and group:
                    self.profile_menu.add(rumps.separator)
                for key, label in group:
                    base, n = label, 2
                    while label in used:  # rumps 以標題當 key，同名要區分
                        label, n = f"{base} {n}", n + 1
                    used.add(label)
                    item = rumps.MenuItem(label, callback=self.pick_profile)
                    item._zh_key = key
                    item.state = int(key == cfg["profile"])
                    self.profile_menu.add(item)

        def sync_profile(self):
            for it in self.profile_menu.values():
                if hasattr(it, "_zh_key"):
                    it.state = int(it._zh_key == cfg["profile"])

        def pick_profile(self, item):
            cfg["profile"] = item._zh_key
            self.sync_profile()
            self.save()
            refresh_key_window()

        def sounds_changed(self):
            self.build_profile_menu()
            refresh_key_window()

        def toggle_bell(self, item):
            cfg["bell"] = not cfg["bell"]
            item.state = int(cfg["bell"])
            self.save()

        def open_keys(self, _item):
            open_settings(cfg, lib, self.save, player.preview, on_profile=self.sync_profile)

        def open_import(self, _item):
            open_import(lib, engine.play, on_changed=self.sounds_changed, on_deleted=self.sound_deleted)

        def open_record(self, _item):
            open_record(engine.play, on_trial=self.set_trial, on_created=self.profile_created,
                        on_deleted=self.sound_deleted, on_changed=self.sounds_changed)

        def set_trial(self, bank):
            player.trial = bank

        def sound_deleted(self, sid):
            library.forget_sound(cfg, sid)  # 目前音效、每鍵自訂若用到它，改回預設
            self.save()
            self.sounds_changed()

        def profile_created(self, sid):
            cfg["profile"] = sid  # 建立後直接切換過去，馬上就能打字聽
            self.save()
            self.build_profile_menu()
            refresh_key_window()

        # ------------------------------------------------ 更新
        def _alert(self, title, text="", buttons=("好",)):
            from AppKit import NSAlert

            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            a = NSAlert.alloc().init()
            a.setMessageText_(title)
            a.setInformativeText_(text)
            for b in buttons:
                a.addButtonWithTitle_(b)
            return int(a.runModal()) - 1000  # 0 = 第一個按鈕

        def _run_bg(self, kind, fn, manual):
            def work():
                try:
                    self._ui_queue.put((kind, fn(), manual))
                except updater.UpdateError as e:
                    self._ui_queue.put(("error", str(e), manual))
                except Exception:  # noqa: BLE001
                    self._ui_queue.put(("error", "發生未預期的錯誤", manual))
            self._busy = True
            threading.Thread(target=work, daemon=True).start()

        def start_check(self, manual):
            if self._busy:
                return
            cfg["last_update_check"] = int(time.time())
            self.save_later()
            self._run_bg("checked", updater.latest_release, manual)

        def update_clicked(self, _item):
            if self._busy:
                return
            if self._latest is None:
                self.start_check(manual=True)
            else:
                self.offer_update(self._latest)

        def toggle_auto_update(self, item):
            cfg["auto_update"] = not cfg["auto_update"]
            item.state = int(cfg["auto_update"])
            self.save()

        def offer_update(self, rel):
            notes = rel.notes[:800] + ("…" if len(rel.notes) > 800 else "")
            bundle = app_bundle()
            if bundle is None:
                if self._alert(f"有新版本 v{rel.version}", notes, ("開啟下載頁面", "稍後")) == 0:
                    from AppKit import NSWorkspace
                    from Foundation import NSURL
                    NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(rel.page_url))
                return
            if self._alert(f"有新版本 v{rel.version}", notes, ("更新並重新啟動", "稍後")) != 0:
                return
            self.update_item.title = "下載更新中…"
            self._run_bg("downloaded", lambda: updater.download_verified(rel), True)

        def handle_ui_queue(self):
            while not self._ui_queue.empty():
                kind, payload, manual = self._ui_queue.get()
                self._busy = False
                if kind == "error":
                    self.update_item.title = (f"更新到 v{self._latest.version}…" if self._latest else "檢查更新…")
                    if manual:
                        self._alert("無法更新", payload)
                elif kind == "checked":
                    if updater.is_newer(payload.version):
                        self._latest = payload
                        self.update_item.title = f"更新到 v{payload.version}…"
                        if manual:
                            self.offer_update(payload)
                    elif manual:
                        self._alert("已是最新版本", f"目前版本 v{V.VERSION}")
                elif kind == "downloaded":
                    try:
                        updater.install_and_relaunch(payload, app_bundle())
                    except updater.UpdateError as e:
                        self.update_item.title = f"更新到 v{self._latest.version}…"
                        self._alert("無法更新", str(e))
                        continue
                    self.quit()

        def poll_secure_input(self, _timer):
            self.handle_ui_queue()
            secure = secure_input_enabled()
            if secure != self.secure:
                self.secure = secure
                tap.set_paused(secure)  # 密碼框：整個停掉監聽
                self.refresh_title()

        def quit(self, _item=None):
            if self._save_timer:
                self._save_timer.cancel()
            self.save()
            engine.stop()
            rumps.quit_application()

    app = ZiHaiApp()
    updater.cleanup_cache()
    if cfg["auto_update"] and time.time() - cfg["last_update_check"] > 24 * 3600:
        threading.Timer(15, lambda: app.start_check(manual=False)).start()
    # Timer 每 0.5 秒回到 Python，Ctrl+C 才能被處理
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    app.run()


# ====================================================================== 自我測試
def run_selftest() -> None:
    print(f"recorded profiles    : {sampler.load_profiles()}")
    engine = AudioEngine()
    engine.start()
    lib = library.SoundLibrary()
    cfg = library.load_config()
    cfg.update(muted=False, keys={})
    player = SwitchPlayer(engine, cfg, lib)
    print(f"audio output latency : {engine.output_latency_ms:.1f} ms")
    print(f"block size           : {BLOCKSIZE} ({BLOCKSIZE / sg.SAMPLE_RATE * 1000:.2f} ms)")
    print(f"secure input now     : {secure_input_enabled()}")

    # 量測 trigger() 本身的耗時（分類→挑樣本→重取樣→排入混音器）
    cost = []
    for _ in range(200):
        t0 = time.perf_counter()
        player.trigger(KeyKind.NORMAL, keycode=0)
        cost.append(time.perf_counter() - t0)
        engine._pending.clear()
    print(f"trigger() cost       : avg {np.mean(cost) * 1e6:.0f} µs, max {np.max(cost) * 1e6:.0f} µs")

    seq = [KeyKind.NORMAL] * 6 + [KeyKind.SPACE, KeyKind.NORMAL, KeyKind.NORMAL,
                                  KeyKind.BACKSPACE, KeyKind.MODIFIER, KeyKind.ENTER]
    for prof, label in sg.PROFILES.items():
        print(f"playing {prof} ({label}) ...")
        cfg["profile"] = prof
        for k in seq:
            player.trigger(k)
            time.sleep(0.07)
            player.trigger(k, up=True)
            time.sleep(0.06)
        time.sleep(0.4)

    print("polyphony: 20 keys @ 25ms interval (rec:blue)")
    cfg["profile"] = "rec:blue"
    for _ in range(20):
        player.trigger(KeyKind.NORMAL)
        time.sleep(0.025)
        player.trigger(KeyKind.NORMAL, up=True)
    time.sleep(0.4)
    engine.stop()
    print("selftest done")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="自嗨軸 ZiHaiSwitch")
    ap.add_argument("--selftest", action="store_true", help="不監聽鍵盤，直接播放全部音效並回報延遲參數")
    args = ap.parse_args()
    run_selftest() if args.selftest else run_app()
