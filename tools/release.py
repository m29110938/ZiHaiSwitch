"""發版工具。

    .venv/bin/python tools/release.py keygen   # 第一次：產生更新簽章金鑰（私鑰只存在本機）
    .venv/bin/python tools/release.py build    # 打包 .app → zip → 簽章，輸出到 dist/

私鑰位置：~/.zihaiswitch/update_signing.key（權限 600，絕不能進 git 或分享）。
私鑰遺失 = 舊版使用者再也無法自動更新；請另外備份到安全的地方。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ed25519  # noqa: E402
import updater  # noqa: E402
import version as V  # noqa: E402

KEY_PATH = Path.home() / ".zihaiswitch" / "update_signing.key"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
PY = sys.executable


def run(cmd: list[str], **kw) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


# ---------------------------------------------------------------- keygen
def keygen() -> None:
    if KEY_PATH.exists():
        sk = bytes.fromhex(KEY_PATH.read_text().strip())
        print(f"金鑰已存在：{KEY_PATH}")
    else:
        KEY_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        sk = secrets.token_bytes(32)
        fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(sk.hex())
        print(f"已產生私鑰：{KEY_PATH}（請備份，勿外流）")
    pk = ed25519.public_key(sk).hex()
    vf = ROOT / "version.py"
    text = re.sub(r'UPDATE_PUBLIC_KEY = "[0-9a-f]*"', f'UPDATE_PUBLIC_KEY = "{pk}"', vf.read_text())
    vf.write_text(text)
    print(f"公鑰已寫入 version.py：{pk}")


# ---------------------------------------------------------------- build
def changelog_section(ver: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text("utf-8")
    m = re.search(rf"^## v?{re.escape(ver)}\b.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def build() -> None:
    if not KEY_PATH.exists():
        sys.exit("找不到簽章私鑰，請先執行 keygen")
    sk = bytes.fromhex(KEY_PATH.read_text().strip())
    if ed25519.public_key(sk).hex() != V.UPDATE_PUBLIC_KEY:
        sys.exit("私鑰與 version.py 的公鑰不符")
    notes = changelog_section(V.VERSION)
    if not notes:
        sys.exit(f"CHANGELOG.md 沒有 v{V.VERSION} 的段落")

    shutil.rmtree(BUILD, ignore_errors=True)
    shutil.rmtree(DIST, ignore_errors=True)
    run([PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
         "--name", "ZiHaiSwitch",
         "--icon", str(ROOT / "assets/icons/AppIcon.icns"),
         "--osx-bundle-identifier", V.BUNDLE_ID,
         "--add-data", f"{ROOT / 'assets/profiles'}:assets/profiles",
         "--add-data", f"{ROOT / 'assets/icons'}:assets/icons",
         # 輕量化：排除用不到的大型套件
         "--exclude-module", "scipy", "--exclude-module", "tkinter",
         "--exclude-module", "unittest", "--exclude-module", "pydoc",
         "--distpath", str(DIST), "--workpath", str(BUILD), "--specpath", str(BUILD),
         str(ROOT / "main.py")], cwd=ROOT)

    app = DIST / "ZiHaiSwitch.app"
    plist_path = app / "Contents" / "Info.plist"
    info = plistlib.loads(plist_path.read_bytes())
    info.update({
        "CFBundleName": "ZiHaiSwitch",
        "CFBundleDisplayName": "自嗨軸",
        "CFBundleShortVersionString": V.VERSION,
        "CFBundleVersion": V.VERSION,
        "LSUIElement": True,                 # 不在 Dock 顯示
        "LSMinimumSystemVersion": "12.0",
        "NSHumanReadableCopyright": "ZiHaiSwitch",
    })
    plist_path.write_bytes(plistlib.dumps(info))
    # 改過 Info.plist 必須重新簽章（ad-hoc）
    run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(app)])
    run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)])

    arch = "arm64" if platform.machine() == "arm64" else "x86_64"
    zname = f"ZiHaiSwitch-{V.VERSION}-macos-{arch}.zip"
    zpath = DIST / zname
    run(["/usr/bin/ditto", "-c", "-k", "--keepParent", str(app), str(zpath)])
    digest = hashlib.sha256(zpath.read_bytes()).hexdigest()
    sig = ed25519.sign(sk, updater.signed_message(V.VERSION, arch, digest)).hex()
    (DIST / (zname + ".sig")).write_text(sig + "\n")
    (DIST / "release-notes.md").write_text(notes + "\n", "utf-8")

    size = zpath.stat().st_size / 1024 / 1024
    print(f"\n完成：{zpath}（{size:.1f} MB）")
    print(f"  SHA-256 {digest}")
    print(f"GitHub Release：tag 設為 v{V.VERSION}，上傳 {zname} 與 {zname}.sig，說明貼 dist/release-notes.md")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["keygen", "build"])
    a = ap.parse_args()
    keygen() if a.cmd == "keygen" else build()
