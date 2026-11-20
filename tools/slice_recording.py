"""把真實錄音切片成取樣軸體。

整段打字錄音：
    .venv/bin/python tools/slice_recording.py red --label 紅軸 --typing red.flac \\
        --source https://freesound.org/people/xxx/sounds/123/ --author xxx --license CC0

單鍵錄音（按下、放開各一檔）：
    .venv/bin/python tools/slice_recording.py brown_rec --label 茶軸 --down press.wav --up release.wav

加打字機鈴聲：再加 --bell bell.wav
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sampler  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="錄音切片 → 取樣軸體")
    ap.add_argument("name", help="軸體代號（英數）")
    ap.add_argument("--label", required=True, help="選單顯示名稱")
    ap.add_argument("--typing", help="整段打字錄音")
    ap.add_argument("--down", help="單鍵按下錄音")
    ap.add_argument("--up", help="單鍵放開錄音")
    ap.add_argument("--bell", help="Enter 鈴聲錄音（打字機用）")
    ap.add_argument("--source", action="append", default=[], help="來源網址（可多次）")
    ap.add_argument("--author", action="append", default=[], help="作者（對應 --source）")
    ap.add_argument("--license", default="CC0", help="授權")
    ap.add_argument("--order", type=int, default=100, help="選單排序")
    ap.add_argument("--user", action="store_true", help="存到使用者資料夾（不隨 App 發佈）")
    a = ap.parse_args()

    if a.typing:
        bank, stats = sampler.bank_from_typing(sampler.read_audio(a.typing))
        print(f"偵測 {stats['detected']} 聲，乾淨可用 {stats['usable']}（按下 {stats['downs']} / 放開 {stats['ups']}）")
    elif a.down:
        up = sampler.read_audio(a.up) if a.up else None
        bank = sampler.bank_from_single(sampler.read_audio(a.down), up)
    else:
        ap.error("需要 --typing 或 --down")
    if a.bell:
        sampler.add_bell(bank, sampler.read_audio(a.bell))

    sources = [{"url": u, "author": (a.author[i] if i < len(a.author) else ""), "license": a.license}
               for i, u in enumerate(a.source)]
    d = sampler.save_profile(a.name, a.label, bank, sources, order=a.order, bundled=not a.user)
    for kind in sorted(bank):
        lens = [f"{len(s) / sampler.SR * 1000:.0f}" for s in bank[kind]]
        print(f"  {kind:14s} x{len(bank[kind])}  ms: {', '.join(lens)}")
    print(f"→ {d}")


if __name__ == "__main__":
    main()
