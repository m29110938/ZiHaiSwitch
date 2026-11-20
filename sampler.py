"""取樣軸體：把真實錄音切成一下一下的按鍵聲，存成可在選單切換的軸體。

軸體資料夾：~/Library/Application Support/ZiHaiSwitch/profiles/<name>/
    profile.json            {"label": 顯示名稱, "sources": [{url, author, license}]}
    <kind>_<nn>.wav         kind = normal / normal_up / space / space_up / enter / enter_up /
                            enter_bell / backspace / backspace_up / modifier / modifier_up
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np

import library
import soundbank as sg
import wavio
from paths import ASSETS

SR = sg.SAMPLE_RATE
BUNDLED_DIR = ASSETS / "profiles"                                         # 隨 App 發佈
PROFILE_DIR = library.APP_DIR / "profiles"                                # 使用者自己切的
ALL_KINDS = [k for kind in sg.KINDS for k in (kind, f"{kind}_up")] + ["enter_bell", "enter_bell_up"]


# ---------------------------------------------------------------- 讀檔
def read_audio(path: str | Path) -> np.ndarray:
    """任意格式 → 48kHz mono float32（macOS afconvert）。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "conv.wav"
        r = subprocess.run(
            ["/usr/bin/afconvert", "-f", "WAVE", "-d", f"LEF32@{SR}", "-c", "1", str(path), str(tmp)],
            capture_output=True, text=True)
        if r.returncode != 0:
            raise ValueError(f"無法讀取音檔：{path}")
        _, data = wavio.read_wav(tmp)
    data = np.asarray(data, dtype=np.float32)
    return data.mean(axis=1) if data.ndim > 1 else data


# ---------------------------------------------------------------- 偵測敲擊
FRAME = int(SR * 0.001)  # 1ms


def _env_db(x: np.ndarray) -> np.ndarray:
    from scipy.signal import butter, sosfilt  # 只有切片工具需要 scipy

    hp = sosfilt(butter(2, 150 / (SR / 2), btype="high", output="sos"), x)
    n = len(hp) // FRAME
    rms = np.sqrt(np.mean(hp[: n * FRAME].reshape(n, FRAME) ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms)


def detect_events(x: np.ndarray, rise_db: float = 12.0, min_gap_ms: int = 25) -> list[dict]:
    """以能量包絡的陡升偵測每一下敲擊。回傳 [{start, end, peak_db, gap_after}]（單位：樣本）。"""
    env = _env_db(x)
    floor = np.percentile(env, 20)
    thr = floor + 18
    onsets = []
    last = -10**9
    for i in range(10, len(env)):
        if env[i] < thr:
            continue
        if env[i] - env[i - 10 : i - 2].min() < rise_db:
            continue
        if i - last < min_gap_ms:
            continue
        # 找到陡升：退回包絡開始上升的位置
        j = i
        while j > i - 8 and env[j - 1] < env[j] - 1:
            j -= 1
        onsets.append(j)
        last = i
    events = []
    for k, on in enumerate(onsets):
        nxt = onsets[k + 1] if k + 1 < len(onsets) else len(env)
        limit = min(nxt - 1, on + 180)
        seg = env[on:limit]
        if len(seg) < 5:
            continue
        peak_i = int(np.argmax(seg))
        tail = np.nonzero(seg[peak_i:] < floor + 6)[0]
        end = on + peak_i + (int(tail[0]) if len(tail) else len(seg) - peak_i)
        events.append(dict(
            start=max(0, (on - 1) * FRAME),
            end=min(len(x), (end + 3) * FRAME),
            peak_db=float(seg[peak_i]),
            gap_after=(nxt - on),          # 與下一聲的間隔（ms）
            clean=(end < limit),           # 尾音在下一聲前已收掉
        ))
    return events


def _centroid(seg: np.ndarray) -> float:
    m = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    f = np.fft.rfftfreq(len(seg), 1 / SR)
    p = m ** 2
    return float((f * p).sum() / (p.sum() + 1e-12))


def _finish(seg: np.ndarray) -> np.ndarray:
    return library.prepare_sample(seg)


# ---------------------------------------------------------------- 由整段打字錄音建 bank
def bank_from_typing(x: np.ndarray, variants: int = 8) -> tuple[dict[str, list[np.ndarray]], dict]:
    events = [e for e in detect_events(x) if e["clean"] and e["gap_after"] >= 35]
    if len(events) < 6:
        raise ValueError(f"可用的敲擊太少（{len(events)} 個），請換一段錄音")
    for e in events:
        seg = x[e["start"] : e["end"]]
        e["centroid"] = _centroid(seg)
        e["dur"] = (e["end"] - e["start"]) / SR
    peaks = np.array([e["peak_db"] for e in events])
    # 觸底（按下）通常比回彈（放開）大聲：以響度中位數分兩群
    cut = float(np.median(peaks))
    downs = sorted([e for e in events if e["peak_db"] >= cut], key=lambda e: e["peak_db"])
    ups = sorted([e for e in events if e["peak_db"] < cut], key=lambda e: e["peak_db"])

    def middle(items, n):
        """取中間響度的 n 個（去掉過大、過小的異常值）。"""
        if len(items) <= n:
            return items
        s = (len(items) - n) // 2
        return items[s : s + n]

    # 大鍵（衛星軸）聲音較低沉、較長：取頻譜重心最低的幾個按下聲
    by_low = sorted(downs, key=lambda e: e["centroid"] - e["dur"] * 2000)
    big = by_low[: max(3, len(downs) // 8)]
    normal = middle([e for e in downs if e not in big], variants)
    quiet_downs = downs[: max(3, len(downs) // 6)]

    # 以一般按下聲的中位數峰值當基準，保留按下／放開／大鍵之間的相對音量
    ref = float(np.median([np.max(np.abs(x[e["start"]:e["end"]])) for e in normal]))

    def cut_out(items):
        out = []
        for e in items:
            seg = x[e["start"] : e["end"]]
            rel = min(1.0, float(np.max(np.abs(seg))) / ref)
            out.append(_finish(seg) * rel)
        return out

    bank = {
        "normal": cut_out(normal),
        "normal_up": cut_out(middle(ups, variants)) or cut_out(normal),
        "space": cut_out(big[:3]),
        "enter": cut_out(big[-3:]),
        "backspace": cut_out(big[1:4] or big),
        "modifier": cut_out(quiet_downs[:3]),
    }
    for k in ("space", "enter", "backspace", "modifier"):
        bank[f"{k}_up"] = bank["normal_up"]
    stats = dict(detected=len(detect_events(x)), usable=len(events), downs=len(downs), ups=len(ups))
    return bank, stats


def _main_hit(x: np.ndarray) -> np.ndarray:
    """單鍵錄音：只取最主要的那一聲，裁掉前後的底噪。"""
    ev = detect_events(x, rise_db=8.0)
    if not ev:
        return x
    e = max(ev, key=lambda e: e["peak_db"])
    return x[e["start"] : e["end"]]


def bank_from_single(down: np.ndarray, up: np.ndarray | None) -> dict[str, list[np.ndarray]]:
    """單鍵錄音（按下、放開各一個檔）：直接當所有按鍵類型的樣本。"""
    down = _main_hit(down)
    up = _main_hit(up) if up is not None else None
    d = [_finish(down)]
    if up is not None:
        rel = min(1.0, float(np.max(np.abs(up))) / float(np.max(np.abs(down))))
        u = [_finish(up) * rel]
    else:
        u = d
    bank = {}
    for kind in sg.KINDS:
        bank[kind] = d
        bank[f"{kind}_up"] = u
    return bank


def add_bell(bank: dict, bell: np.ndarray, delay: float = 0.06) -> None:
    b = _finish(bell)
    fade = min(len(b), int(SR * 0.4))  # 長尾音慢慢淡出，避免截斷感
    b[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32) ** 2
    out = []
    for e in bank["enter"]:
        n = max(len(e), int(delay * SR) + len(b))
        buf = np.zeros(n, dtype=np.float32)
        buf[: len(e)] += e
        buf[int(delay * SR) : int(delay * SR) + len(b)] += b * 0.6
        out.append(_finish(buf))
    bank["enter_bell"] = out
    bank["enter_bell_up"] = bank["enter_up"]


# ---------------------------------------------------------------- 存讀
def save_profile(name: str, label: str, bank: dict, sources: list[dict],
                 order: int = 100, bundled: bool = True) -> Path:
    d = (BUNDLED_DIR if bundled else PROFILE_DIR) / name
    d.mkdir(parents=True, exist_ok=True)
    for f in d.glob("*.wav"):
        f.unlink()
    for kind, samples in bank.items():
        for i, s in enumerate(samples):
            wavio.write_wav(d / f"{kind}_{i:02d}.wav", SR, s.astype(np.float32))
    (d / "profile.json").write_text(
        json.dumps({"label": label, "order": order, "sources": sources}, ensure_ascii=False, indent=2),
        "utf-8")
    return d


def load_profiles() -> int:
    """把內建與使用者的取樣軸體註冊進 sound_generator，選單就會出現。回傳數量。"""
    found = []
    for base in (BUNDLED_DIR, PROFILE_DIR):
        if not base.is_dir():
            continue
        for d in base.iterdir():
            meta = d / "profile.json"
            if not meta.is_file():
                continue
            try:
                info = json.loads(meta.read_text("utf-8"))
                found.append((int(info.get("order", 100)), info["label"], d))
            except (OSError, ValueError, KeyError):
                continue
    count = 0
    for _, label, d in sorted(found, key=lambda t: (t[0], t[2].name)):
        bank: dict[str, list[np.ndarray]] = {}
        for f in sorted(d.glob("*.wav")):
            kind = f.stem.rsplit("_", 1)[0]
            sr, data = wavio.read_wav(f)
            if sr == SR:
                bank.setdefault(kind, []).append(np.asarray(data, dtype=np.float32))
        if all(k in bank for k in ALL_KINDS[:10]):
            sg.register_bank(f"rec:{d.name}", label, bank)
            count += 1
    return count
