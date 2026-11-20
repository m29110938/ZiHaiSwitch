"""產生 8-bit 像素風圖示（手工點陣，無漸層、無抗鋸齒）。

    .venv/bin/python tools/make_icons.py

輸出：
    assets/icons/status_{on,muted,secure}.png / @2x.png   狀態列 template 圖示（黑色 + 透明）
    assets/icons/AppIcon.png (1024) 與 AppIcon.icns        App 圖示
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "icons"

# PICO-8 風格的 16 色有限色盤（只取用到的）
PAL = {
    ".": (0, 0, 0, 0),
    "K": (0, 0, 0, 255),          # 外框
    "N": (29, 43, 83, 255),       # 深藍底
    "n": (18, 26, 52, 255),       # 底色陰影
    "W": (255, 241, 232, 255),    # 鍵帽頂面
    "G": (194, 195, 199, 255),    # 鍵帽側面
    "D": (95, 87, 79, 255),       # 鍵帽底部陰影
    "R": (255, 0, 77, 255),       # 音符
    "Y": (255, 236, 39, 255),     # 敲擊火花
    "O": (255, 163, 0, 255),      # 火花外圈
    "B": (41, 173, 255, 255),     # 底框亮邊
}


# ---------------------------------------------------------------- PNG 輸出（免第三方套件）
def write_png(path: Path, rgba: np.ndarray) -> None:
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[y].astype(np.uint8).tobytes() for y in range(h))

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def render(grid: list[str], scale: int) -> np.ndarray:
    h, w = len(grid), len(grid[0])
    img = np.zeros((h, w, 4), dtype=np.uint8)
    for y, row in enumerate(grid):
        assert len(row) == w, f"row {y} width {len(row)} != {w}"
        for x, ch in enumerate(row):
            img[y, x] = PAL[ch]
    return img.repeat(scale, 0).repeat(scale, 1)  # 最近鄰放大，保持硬邊


# ---------------------------------------------------------------- 狀態列（18×18 template）
# 8-bit 喇叭：左側喇叭本體（x=0..8），右側 x=10..17 依狀態畫聲波 / ✕ / 鎖
_SPEAKER = [
    "..................",
    "..................",
    "..................",
    "........#.........",
    ".......##.........",
    "......###.........",
    ".########.........",
    ".########.........",
    ".########.........",
    ".########.........",
    ".########.........",
    ".########.........",
    "......###.........",
    ".......##.........",
    "........#.........",
    "..................",
    "..................",
    "..................",
]

_OVERLAY = {  # (x, y) 像素，x 範圍 10..17
    "on": [(10, 7), (11, 8), (11, 9), (10, 10),                       # 小聲波
           (12, 4), (13, 5), (14, 6), (14, 7), (14, 8), (14, 9),     # 大聲波
           (14, 10), (14, 11), (13, 12), (12, 13)],
    "muted": [(11 + i, 6 + i) for i in range(6)] + [(16 - i, 6 + i) for i in range(6)],
    "secure": [(13, 4), (14, 4), (12, 5), (15, 5), (12, 6), (15, 6), (12, 7), (15, 7)]
              + [(x, y) for y in range(8, 14) for x in range(11, 17)
                 if not (x in (13, 14) and y in (10, 11))],
}


def status_grid(state: str) -> list[str]:
    grid = [list(r) for r in _SPEAKER]
    for x, y in _OVERLAY[state]:
        grid[y][x] = "#"
    return ["".join(r).replace("#", "K") for r in grid]


# ---------------------------------------------------------------- App 圖示（32×32）
APP = [
    "................................",
    "................................",
    "....KKKKKKKKKKKKKKKKKKKKKKKK....",
    "...KBBBBBBBBBBBBBBBBBBBBBBBBK...",
    "..KBNNNNNNNNNNNNNNNONNNNONNNNK..",
    "..KBNNNNNNNNNNNNNNNYNNNYNNNNNK..",
    "..KBNNNNNNNNNNNNNNNNNNNNNNNNNK..",
    "..KBNNNNNKKKKKKKKKKKKKNNNNNNNK..",
    "..KBNNNNKGWWWWWWWWWWWGKNNYONNK..",
    "..KBNNNKGWWWWWWWWWWWWWGKNNNNNK..",
    "..KBNNNKGWWWWWWWRRRWWWGKNNNNNK..",
    "..KBNNNKGWWWWWWWRWRRWWGKNNNNNK..",
    "..KBNNNKGWWWWWWWRWWRWWGKNNNNNK..",
    "..KBNNNKGWWWWWWWRWWWWWGKNNNNNK..",
    "..KBNNNKGWWWWWWWRWWWWWGKNNNNNK..",
    "..KBNNNKGWWWWRRRRWWWWWGKNNNNNK..",
    "..KBNNNKGWWWRRRRRWWWWWGKNNNNNK..",
    "..KBNNNKGWWWWRRRWWWWWWGKNNNNNK..",
    "..KBNNNKGWWWWWWWWWWWWWGKNNNNNK..",
    "..KBNNNKGGWWWWWWWWWWWGGKNNNNNK..",
    "..KBNNNKGGGGGGGGGGGGGGGKNNNNNK..",
    "..KBNNNKGGGGGGGGGGGGGGGKNNNNNK..",
    "..KBNNNKGGGGGGGGGGGGGGGKnNNNNK..",
    "..KBNNNKDDDDDDDDDDDDDDDKnNNNNK..",
    "..KBNNNNKKKKKKKKKKKKKKKnnNNNNK..",
    "..KBNNNNNnnnnnnnnnnnnnnnNNNNNK..",
    "..KBNNNNNNNNNNNNNNNNNNNNNNNNNK..",
    "..KBNNNNNNNNNNNNNNNNNNNNNNNNNK..",
    "...KNNNNNNNNNNNNNNNNNNNNNNNNK...",
    "....KKKKKKKKKKKKKKKKKKKKKKKK....",
    "................................",
    "................................",
]


def make_icns(app_png_1024: np.ndarray) -> None:
    if not shutil.which("iconutil"):
        return
    with tempfile.TemporaryDirectory() as td:
        iconset = Path(td) / "AppIcon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for mult, suffix in ((1, ""), (2, "@2x")):
                px = size * mult
                scale = max(1, px // 32)
                img = render(APP, scale) if px >= 32 else render(APP, 1)[:: 32 // px, :: 32 // px]
                write_png(iconset / f"icon_{size}x{size}{suffix}.png", img)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT / "AppIcon.icns")],
                       check=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for state in _OVERLAY:
        g = status_grid(state)
        write_png(OUT / f"status_{state}.png", render(g, 1))
        write_png(OUT / f"status_{state}@2x.png", render(g, 2))
    big = render(APP, 32)
    write_png(OUT / "AppIcon.png", big)
    make_icns(big)
    for p in sorted(OUT.iterdir()):
        print(p.relative_to(ROOT))


if __name__ == "__main__":
    main()
