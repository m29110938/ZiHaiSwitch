"""設定檔與音效庫：設定讀寫、匯入音效（afconvert）、刪除與名稱管理。

設定與匯入音效存放於 ~/Library/Application Support/ZiHaiSwitch/
    config.json   使用者設定（音量、軸體、每鍵自訂…）；不含任何打字內容
    sounds/*.wav  匯入後轉成 48kHz mono float32 的音效

音效 ID：
    "default"                     跟隨目前軸體
    "none"                        無聲
    "builtin:<profile>:<kind>"    內建合成音（放開時自動用 <kind>_up）
    "user:<name>"                 匯入音效
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import numpy as np

import soundbank as sg
import wavio

APP_DIR = Path.home() / "Library" / "Application Support" / "ZiHaiSwitch"
SOUND_DIR = APP_DIR / "sounds"
NAMES_PATH = SOUND_DIR / "names.json"   # 匯入音效的顯示名稱 {"user:<檔名>": "名稱"}
CONFIG_PATH = APP_DIR / "config.json"

MAX_IMPORT_SECONDS = 2.0
IMPORT_PEAK = 0.6

DEFAULT_CONFIG = {
    "volume": 65,          # 0～100
    "profile": "rec:brown",
    "muted": False,
    "bell": True,          # 打字機 Enter 鈴聲
    "keys": {},            # {"<keycode>": {"down": <sound id>, "up": <sound id>}}
    "auto_update": True,   # 每天最多自動檢查一次新版本
    "last_update_check": 0,
}

KIND_LABELS = {
    "normal": "一般鍵",
    "space": "空白鍵",
    "enter": "Enter",
    "enter_bell": "Enter＋鈴聲",
    "backspace": "Backspace",
    "modifier": "修飾鍵",
}


# ---------------------------------------------------------------- 設定
def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    except (OSError, ValueError):
        pass
    cfg["volume"] = int(min(100, max(0, int(cfg.get("volume", 65)))))
    # 舊版取樣軸體代號 rec:<x>_rec → rec:<x>
    def _migrate(sid):
        if isinstance(sid, str) and "_rec" in sid:
            return sid.replace("_rec:", ":").removesuffix("_rec")
        return sid
    cfg["profile"] = _migrate(cfg["profile"])
    cfg["keys"] = {k: {w: _migrate(v) for w, v in ov.items()} for k, ov in (cfg["keys"] if isinstance(cfg.get("keys"), dict) else {}).items()
                   if isinstance(ov, dict)}
    if cfg["profile"] not in sg.PROFILES:
        cfg["profile"] = next(iter(sg.PROFILES), "")
    if not isinstance(cfg.get("keys"), dict):
        cfg["keys"] = {}
    return cfg


_save_lock = threading.Lock()


def save_config(cfg: dict) -> None:
    with _save_lock:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, CONFIG_PATH)


# ---------------------------------------------------------------- 匯入
def _unique_name(stem: str) -> str:
    stem = "".join(c for c in stem if c not in '/\\:\0').strip() or "sound"
    name, i = stem, 2
    while (SOUND_DIR / f"{name}.wav").exists():
        name, i = f"{stem}-{i}", i + 1
    return name


def prepare_sample(data: np.ndarray) -> np.ndarray:
    """去掉開頭靜音（避免延遲）、截長度、正規化音量。"""
    data = np.asarray(data, dtype=np.float32)
    if data.ndim > 1:
        data = data.mean(axis=1)
    peak = float(np.max(np.abs(data))) if len(data) else 0.0
    if peak <= 1e-6:
        raise ValueError("音檔沒有聲音")
    thr = peak * 10 ** (-45 / 20)
    loud = np.nonzero(np.abs(data) > thr)[0]
    start = max(0, loud[0] - int(sg.SAMPLE_RATE * 0.001))
    end = loud[-1] + 1
    data = data[start:end][: int(sg.SAMPLE_RATE * MAX_IMPORT_SECONDS)]
    fade = min(int(sg.SAMPLE_RATE * 0.004), len(data))
    data[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return (data / peak * IMPORT_PEAK).astype(np.float32)


def default_name(stem: str) -> str:
    """freesound 檔名「123__作者__cat-meow」→「cat meow」。"""
    parts = stem.split("__")
    if len(parts) >= 3 and parts[0].isdigit():
        stem = "__".join(parts[2:])
    return stem.replace("_", " ").replace("-", " ").strip() or "音效"


def load_names() -> dict[str, str]:
    try:
        data = json.loads(NAMES_PATH.read_text("utf-8"))
        return {k: str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def set_name(sid: str, name: str) -> None:
    name = name.strip()
    if not name:
        return
    names = load_names()
    names[sid] = name
    SOUND_DIR.mkdir(parents=True, exist_ok=True)
    tmp = NAMES_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(names, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, NAMES_PATH)


def import_sound(src: str | Path, name: str | None = None) -> str:
    """把任意 macOS 支援的音檔（wav/aiff/mp3/m4a/caf…）轉檔匯入，回傳音效 ID。"""
    src = Path(src)
    SOUND_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "conv.wav"
        r = subprocess.run(
            ["/usr/bin/afconvert", "-f", "WAVE", "-d", f"LEF32@{sg.SAMPLE_RATE}", "-c", "1",
             str(src), str(tmp)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise ValueError("無法讀取這個音檔格式")
        _, data = wavio.read_wav(tmp)
    data = prepare_sample(data)
    fname = _unique_name(src.stem)
    wavio.write_wav(SOUND_DIR / f"{fname}.wav", sg.SAMPLE_RATE, data)
    sid = f"user:{fname}"
    set_name(sid, name or default_name(src.stem))
    return sid


# ---------------------------------------------------------------- 音效庫
class SoundLibrary:
    def __init__(self):
        self._user: dict[str, list[np.ndarray]] = {}
        self.reload_user()

    def reload_user(self) -> None:
        loaded = {}
        if SOUND_DIR.is_dir():
            for f in sorted(SOUND_DIR.glob("*.wav")):
                try:
                    sr, data = wavio.read_wav(f)
                    if sr == sg.SAMPLE_RATE:
                        loaded[f"user:{f.stem}"] = [np.asarray(data, dtype=np.float32)]
                except (OSError, ValueError):
                    continue
        self._user = loaded
        self.names = load_names()
        # 匯入音效也當成一種「音效」：所有鍵按下都播它，放開無聲
        for sid, samples in loaded.items():
            bank = {}
            for kind in sg.KINDS:
                bank[kind] = samples
                bank[f"{kind}_up"] = []
            sg.register_bank(sid, self.display_name(sid), bank)

    def display_name(self, sid: str) -> str:
        return self.names.get(sid) or default_name(sid.split(":", 1)[1])

    def builtin_options(self) -> list[tuple[str, str]]:
        opts = []
        for prof, plabel in sg.PROFILES.items():
            if prof.startswith("user:"):
                continue
            for kind in sg.get_bank(prof):
                if kind.endswith("_up"):
                    continue
                opts.append((f"builtin:{prof}:{kind}", f"{plabel}・{KIND_LABELS[kind]}"))
        return opts

    def user_options(self) -> list[tuple[str, str]]:
        return [(sid, self.display_name(sid)) for sid in self._user]

    def label(self, sid: str) -> str:
        if sid in ("default", None):
            return "預設"
        if sid == "none":
            return "無聲"
        for k, v in self.builtin_options() + self.user_options():
            if k == sid:
                return v
        return "預設"

    def samples(self, sid: str, up: bool) -> list[np.ndarray] | None:
        """回傳樣本變體；None 表示找不到（呼叫端改用預設）。"""
        if sid.startswith("builtin:"):
            try:
                prof, kind = sid[len("builtin:"):].rsplit(":", 1)
                return sg.get_bank(prof)[f"{kind}_up" if up else kind]
            except (KeyError, ValueError):
                return None
        if sid.startswith("user:"):
            return self._user.get(sid)
        return None


# ---------------------------------------------------------------- 刪除
def delete_user_sound(sid: str) -> None:
    """刪除匯入音效（檔案與名稱）。"""
    if not sid.startswith("user:"):
        return
    f = SOUND_DIR / f"{sid[len('user:'):]}.wav"
    if f.parent == SOUND_DIR and f.is_file():
        f.unlink()
    names = load_names()
    if names.pop(sid, None) is not None:
        NAMES_PATH.write_text(json.dumps(names, ensure_ascii=False, indent=2), "utf-8")
    sg.unregister(sid)


def forget_sound(cfg: dict, sid: str) -> None:
    """音效被刪除後，清掉設定裡引用它的地方（目前音效、每鍵自訂）。"""
    if cfg.get("profile") == sid:
        cfg["profile"] = DEFAULT_CONFIG["profile"] if DEFAULT_CONFIG["profile"] in sg.PROFILES \
            else next(iter(sg.PROFILES), "")
    refs = (sid,)
    prefix = f"builtin:{sid}:"
    for key in list(cfg.get("keys", {})):
        ov = cfg["keys"][key]
        for w in list(ov):
            if ov[w] in refs or str(ov[w]).startswith(prefix):
                del ov[w]
        if not ov:
            del cfg["keys"][key]
