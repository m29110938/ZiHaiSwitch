"""資源路徑：開發時是專案資料夾，打包成 .app 後是 bundle 內的資源資料夾。"""

from __future__ import annotations

import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ASSETS = RESOURCE_DIR / "assets"


def app_bundle() -> Path | None:
    """打包版回傳 .app 路徑（…/ZiHaiSwitch.app），開發版回傳 None。"""
    if not FROZEN:
        return None
    for p in Path(sys.executable).resolve().parents:
        if p.suffix == ".app":
            return p
    return None
