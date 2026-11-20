"""音效註冊表（執行時用，只依賴 numpy）。

PROFILES：App 選單可選的音效 {ID: 顯示名稱}
    rec:<name>   隨 App 發佈或使用者切片的真實錄音軸體（sampler.load_profiles 註冊）
    user:<name>  使用者匯入的單一音效（library.SoundLibrary 註冊）
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 48000
KINDS = ("normal", "space", "enter", "backspace", "modifier")

PROFILES: dict[str, str] = {}
_REGISTERED: dict[str, dict[str, list[np.ndarray]]] = {}


def register_bank(name: str, label: str, bank: dict[str, list[np.ndarray]]) -> None:
    _REGISTERED[name] = bank
    PROFILES[name] = label


def unregister(name: str) -> None:
    _REGISTERED.pop(name, None)
    PROFILES.pop(name, None)


def get_bank(profile: str) -> dict[str, list[np.ndarray]]:
    if profile not in _REGISTERED:
        raise ValueError(f"unknown profile: {profile}")
    return _REGISTERED[profile]
