"""程序化軸體音效合成器（免外部音檔）。

合成方式：極短的噪音／脈衝「激發」→ 一組高阻尼的二階共振器（模態合成）。
真實鍵盤聲是塑膠撞擊的寬頻瞬態，不是有音高的正弦波；每次激發的噪音不同，
所以同一組共振器每次敲出來的聲音都略有差異，比較有機。

所有樣本皆為 mono float32、取樣率 SAMPLE_RATE，結果快取在記憶體。

用法：
    from sound_generator import get_bank
    bank = get_bank("brown")   # {"normal": [...], "normal_up": [...], "space": [...], ...}
    # 打字機另有 "enter_bell"（Enter + 叮）

    python sound_generator.py --export   # 將全部音效輸出成 assets/*.wav 試聽
"""

from __future__ import annotations

import argparse
import zlib
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, lfilter, sosfilt

from soundbank import KINDS, SAMPLE_RATE

NYQ = SAMPLE_RATE / 2

# 程序化合成的軸體：App 不使用，只保留 `--export` 試聽
SYNTH_PROFILES = {
    "brown": "茶軸",
    "blue": "青軸",
    "typewriter": "打字機",
}

# 各按鍵類型：pitch 共振頻率倍率、decay 衰減倍率、gain 音量、stab 是否有衛星軸鋼絲聲
KIND_PARAMS = {
    "normal": dict(pitch=1.00, decay=1.0, gain=1.00, stab=False),
    "space": dict(pitch=0.66, decay=1.7, gain=1.00, stab=True),
    "enter": dict(pitch=0.78, decay=1.4, gain=1.00, stab=True),
    "backspace": dict(pitch=0.86, decay=1.25, gain=0.95, stab=False),
    "modifier": dict(pitch=1.05, decay=0.9, gain=0.55, stab=False),
}

UP_GAIN = 0.38      # 放開（回彈）音量，相對按下
VARIANTS = 8


# ---------------------------------------------------------------- 基本元件
def _n(dur: float) -> int:
    return max(1, int(SAMPLE_RATE * dur))


def excitation(rng, dur: float = 0.0015, tilt: float = 0.0) -> np.ndarray:
    """撞擊激發：前沿脈衝 + 快速衰減白噪音。tilt>0 偏亮、<0 偏悶。"""
    n = _n(dur)
    env = np.exp(-np.arange(n) / (n / 4.0))
    exc = rng.standard_normal(n) * env
    exc[0] += 3.0
    if tilt > 0:    # 加入一階差分 → 高頻更多
        exc = exc * (1 - tilt) + np.diff(exc, prepend=0.0) * tilt
    elif tilt < 0:  # 一階低通 → 偏悶
        exc = lfilter([1 + tilt], [1, tilt], exc)
    return exc


def resonate(exc: np.ndarray, length: float, modes) -> np.ndarray:
    """平行二階共振器組。modes = [(頻率Hz, 衰減秒, 增益), ...]"""
    n = _n(length)
    x = np.zeros(n)
    x[: min(n, len(exc))] = exc[:n]
    out = np.zeros(n)
    for freq, decay, gain in modes:
        freq = min(freq, NYQ * 0.92)
        r = np.exp(-1.0 / (max(decay, 1e-4) * SAMPLE_RATE))
        w = 2 * np.pi * freq / SAMPLE_RATE
        b = [(1 - r * r) * 0.5, 0, -(1 - r * r) * 0.5]  # 帶通形式，無 DC
        a = [1, -2 * r * np.cos(w), r * r]
        out += lfilter(b, a, x) * gain
    return out


def band_noise(rng, dur, lo, hi, decay) -> np.ndarray:
    lo = float(np.clip(lo, 30, NYQ * 0.8))
    hi = float(np.clip(hi, lo * 1.3, NYQ * 0.95))
    sos = butter(2, [lo / NYQ, hi / NYQ], btype="band", output="sos")
    sig = sosfilt(sos, rng.standard_normal(_n(dur)))
    sig /= np.max(np.abs(sig)) + 1e-9
    return sig * np.exp(-np.arange(len(sig)) / (decay * SAMPLE_RATE))


def place(buf: np.ndarray, sig: np.ndarray, offset: float, gain: float) -> None:
    start = _n(offset) if offset > 0 else 0
    if start >= len(buf):
        return
    end = min(len(buf), start + len(sig))
    buf[start:end] += sig[: end - start] * gain


def housing(rng, sig: np.ndarray, amount: float, lp: float = 3500) -> np.ndarray:
    """外殼／桌面殘響：與 12ms 衰減噪音 IR 摺積，讓聲音「在一個殼裡」。"""
    ir = band_noise(rng, 0.012, 150, lp, 0.003)
    ir[0] = 0
    wet = np.convolve(sig, ir)[: len(sig)]
    wet /= np.max(np.abs(wet)) + 1e-9
    return sig / (np.max(np.abs(sig)) + 1e-9) + wet * amount


def finalize(sig: np.ndarray, peak: float = 0.6, gain: float = 1.0) -> np.ndarray:
    sos = butter(2, 70 / NYQ, btype="high", output="sos")
    sig = sosfilt(sos, sig)
    # 去掉尾端靜音，縮短樣本
    thr = np.max(np.abs(sig)) * 10 ** (-60 / 20)
    idx = np.nonzero(np.abs(sig) > thr)[0]
    if len(idx):
        sig = sig[: idx[-1] + 1]
    fade = min(_n(0.004), len(sig))
    sig[-fade:] *= np.linspace(1.0, 0.0, fade)
    sig = sig / (np.max(np.abs(sig)) + 1e-9) * peak * gain
    return sig.astype(np.float32)


def _lp(sig: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    sos = butter(order, min(cutoff, NYQ * 0.9) / NYQ, btype="low", output="sos")
    return sosfilt(sos, sig)


def _jit(rng, x, amt=0.04):
    return x * rng.uniform(1 - amt, 1 + amt)


def _stab(rng, buf, decay):
    """衛星軸鋼絲的細碎金屬聲（大鍵）。"""
    for off in (0.002 + rng.uniform(0, 0.002), 0.010 + rng.uniform(0, 0.004)):
        tick = resonate(excitation(rng, 0.0006), 0.03, [(_jit(rng, 3400), 0.004 * decay, 1.0),
                                                       (_jit(rng, 5600), 0.003 * decay, 0.6)])
        place(buf, tick, off, 0.05)


def _spring(rng, buf, offset, gain=0.012):
    """彈簧殘響：極微弱的高頻金屬餘音。"""
    ping = resonate(excitation(rng, 0.0005), 0.05, [(_jit(rng, 6900, 0.06), 0.018, 1.0),
                                                     (_jit(rng, 9300, 0.06), 0.010, 0.5)])
    place(buf, ping, offset, gain)


# ---------------------------------------------------------------- 軸體配方
def synth_brown(rng, pitch, decay, gain, stab, up=False) -> np.ndarray:
    """茶軸：輕微段落摩擦 + 中頻塑膠觸底（偏圓潤的 clack/thock）。"""
    p, d = pitch, decay
    buf = np.zeros(_n(0.09 * d))
    if up:
        # 回彈：鍵帽撞回上限位，比觸底高、薄、輕
        modes = [(_jit(rng, 900 * p), 0.004 * d, 1.0), (_jit(rng, 2100 * p), 0.0025 * d, 0.4),
                 (_jit(rng, 3800 * p), 0.0015 * d, 0.1)]
        place(buf, resonate(excitation(rng, 0.001), 0.05 * d, modes), 0, 1.0)
        return finalize(_lp(housing(rng, buf, 0.2), 4500 * p ** 0.5), gain=gain * UP_GAIN)
    # 段落 bump：極短、低音量的摩擦
    place(buf, band_noise(rng, 0.006, 1200, 4500, 0.0012), 0, 0.06)
    bo = 0.006 + rng.uniform(0, 0.003)
    modes = [
        (_jit(rng, 240 * p), 0.007 * d, 0.30),   # 外殼低頻
        (_jit(rng, 640 * p), 0.0055 * d, 1.00),  # 鍵帽主體
        (_jit(rng, 1450 * p), 0.0035 * d, 0.40),
        (_jit(rng, 2900 * p), 0.0020 * d, 0.12),
        (_jit(rng, 5000 * p), 0.0010 * d, 0.03),
    ]
    place(buf, resonate(excitation(rng, 0.0018, tilt=-0.3), 0.08 * d, modes), bo, 1.0)
    _spring(rng, buf, bo)
    if stab:
        _stab(rng, buf, d)
    return finalize(_lp(housing(rng, buf, 0.3), 3800 * p ** 0.5), gain=gain)


def synth_blue(rng, pitch, decay, gain, stab, up=False) -> np.ndarray:
    """青軸：click jacket 撞擊的尖銳 click → 數 ms 後的觸底 clack（兩段式）。"""
    p, d = pitch, decay
    hp = p ** 0.4  # 大鍵的 click 只微降，保留清脆
    buf = np.zeros(_n(0.08 * d))
    click_modes = [(_jit(rng, 3300 * hp), 0.0016, 1.0), (_jit(rng, 5400 * hp), 0.0010, 0.30),
                   (_jit(rng, 2200 * hp), 0.0020, 0.45), (_jit(rng, 8200 * hp), 0.0006, 0.05)]
    if up:
        # 放開時 click jacket 回位，也會有一聲小 click
        place(buf, resonate(excitation(rng, 0.0005, tilt=0.5), 0.03, click_modes), 0, 0.7)
        place(buf, resonate(excitation(rng, 0.001), 0.04, [(_jit(rng, 1900 * p), 0.003 * d, 1.0),
                                                           (_jit(rng, 3600 * p), 0.002 * d, 0.5)]),
              0.004, 0.5)
        return finalize(_lp(housing(rng, buf, 0.15), 6500), gain=gain * UP_GAIN * 1.2)
    place(buf, resonate(excitation(rng, 0.0005, tilt=0.3), 0.03, click_modes), 0, 1.0)
    bo = 0.008 + rng.uniform(0, 0.003)
    modes = [(_jit(rng, 820 * p), 0.0045 * d, 1.0), (_jit(rng, 1900 * p), 0.003 * d, 0.45),
             (_jit(rng, 3600 * p), 0.0018 * d, 0.12), (_jit(rng, 300 * p), 0.006 * d, 0.35)]
    place(buf, resonate(excitation(rng, 0.0012), 0.06 * d, modes), bo, 0.75)
    _spring(rng, buf, bo, 0.015)
    if stab:
        _stab(rng, buf, d)
    return finalize(_lp(housing(rng, buf, 0.15), 6500), gain=gain)


def _bell(rng, dur=1.2) -> np.ndarray:
    """打字機 Enter 專屬「叮」：小鈴的非諧和泛音。"""
    base = _jit(rng, 2350, 0.005)
    exc = excitation(rng, 0.0008)
    return resonate(exc, dur, [(base, 0.45, 1.0), (base * 2.32, 0.22, 0.5),
                               (base * 4.25, 0.10, 0.25), (base * 0.5, 0.30, 0.12)])


def synth_typewriter(rng, pitch, decay, gain, stab, up=False, bell=False) -> np.ndarray:
    """打字機：字錘撞擊滾筒的金屬「啪」+ 機身 thunk + 連桿回位。"""
    p, d = pitch, decay
    buf = np.zeros(_n(0.16 * d + (1.3 if bell else 0)))
    if up:
        # 按鍵桿回彈：木/金屬的「嗒」
        place(buf, resonate(excitation(rng, 0.001), 0.06, [(_jit(rng, 1100 * p), 0.006, 1.0),
                                                           (_jit(rng, 2600 * p), 0.004, 0.5)]),
              0, 1.0)
        return finalize(_lp(housing(rng, buf, 0.35, lp=2500), 5000), gain=gain * UP_GAIN)
    has_type_bar = p >= 0.75  # 空白鍵沒有字錘
    # 按鍵桿下壓
    place(buf, resonate(excitation(rng, 0.001), 0.05, [(_jit(rng, 700 * p), 0.006, 1.0),
                                                       (_jit(rng, 1600 * p), 0.004, 0.5)]), 0, 0.35)
    hit = 0.018 + rng.uniform(0, 0.006)  # 字錘飛行時間
    if has_type_bar:
        metal = [(_jit(rng, 1850), 0.009, 1.0), (_jit(rng, 3150), 0.012, 0.7),
                 (_jit(rng, 4650), 0.006, 0.45), (_jit(rng, 960), 0.014, 0.35),
                 (_jit(rng, 7200), 0.003, 0.25)]
        place(buf, resonate(excitation(rng, 0.0008, tilt=0.4), 0.08, metal), hit, 1.0)
    # 機身／滾筒 thunk
    body = [(_jit(rng, 130 * p / 0.9), 0.020 * d, 1.0), (_jit(rng, 420 * p), 0.010 * d, 0.6)]
    place(buf, resonate(excitation(rng, 0.003, tilt=-0.6), 0.12 * d, body), hit, 0.55)
    # 連桿／字錘回位的小碰撞
    place(buf, resonate(excitation(rng, 0.0006), 0.04, [(_jit(rng, 2400), 0.004, 1.0)]),
          hit + 0.030 + rng.uniform(0, 0.012), 0.18)
    if stab:
        _stab(rng, buf, d)
    if bell:
        # 換行：滑架拉回的摩擦聲 + 叮
        place(buf, band_noise(rng, 0.20, 500, 2500, 0.07), 0.07, 0.15)
        place(buf, _bell(rng), 0.08, 0.35)
    return finalize(_lp(housing(rng, buf, 0.35, lp=2500), 6000 if not bell else 9000), gain=gain)


SYNTHS = {
    "brown": synth_brown,
    "blue": synth_blue,
    "typewriter": synth_typewriter,
}


# ---------------------------------------------------------------- 對外 API
@lru_cache(maxsize=None)
def synth_bank(profile: str) -> dict[str, list[np.ndarray]]:
    """程序化合成的音效（只供 --export 試聽）。"""
    if profile not in SYNTHS:
        raise ValueError(f"unknown profile: {profile}")
    synth = SYNTHS[profile]
    rng = np.random.default_rng(zlib.crc32(profile.encode()))
    bank: dict[str, list[np.ndarray]] = {}
    for kind in KINDS:
        params = KIND_PARAMS[kind]
        bank[kind] = [synth(rng, **params) for _ in range(VARIANTS)]
        bank[f"{kind}_up"] = [synth(rng, **params, up=True) for _ in range(VARIANTS)]
    if profile == "typewriter":
        # Enter + 換行鈴聲（可在選單關閉）
        bank["enter_bell"] = [synth(rng, **KIND_PARAMS["enter"], bell=True) for _ in range(VARIANTS)]
        bank["enter_bell_up"] = bank["enter_up"]
    return bank


def export_wavs(out_dir: Path) -> list[Path]:
    """每種音效輸出一個 wav：把 8 個變體以 120ms 間隔串起來，方便試聽連打感。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for profile in SYNTHS:
        for kind, variants in synth_bank(profile).items():
            gap = _n(0.12)
            total = gap * len(variants) + max(len(v) for v in variants)
            buf = np.zeros(total, dtype=np.float32)
            for i, v in enumerate(variants):
                buf[i * gap : i * gap + len(v)] += v
            path = out_dir / f"{profile}_{kind}.wav"
            wavfile.write(path, SAMPLE_RATE, (np.clip(buf, -1, 1) * 32767).astype(np.int16))
            written.append(path)
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="ZiHaiSwitch 程序化音效合成器")
    ap.add_argument("--export", action="store_true", help="輸出 wav 到 assets/")
    args = ap.parse_args()
    if args.export:
        for p in export_wavs(Path(__file__).parent / "assets"):
            print(p)
    else:
        import time

        t0 = time.perf_counter()
        for prof in SYNTH_PROFILES:
            bank = synth_bank(prof)
            print(prof, {k: f"{len(v[0]) / SAMPLE_RATE * 1000:.0f}ms" for k, v in bank.items()})
        print(f"synth time: {(time.perf_counter() - t0) * 1000:.1f} ms")
