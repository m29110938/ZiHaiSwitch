"""檢查與安裝更新（GitHub Releases）。

資安設計：
- 只連 HTTPS，且只接受 GitHub 網域（含轉址後的最終網址）；憑證由系統驗證。
- 更新檔必須有發版者的 Ed25519 簽章，簽章內容綁定「版本＋CPU 架構＋SHA-256」，
  公鑰內建於 App（version.UPDATE_PUBLIC_KEY）。簽章不符一律拒絕。
- 只接受比目前更新的版本（防降版攻擊）。
- 解壓後再檢查 bundle ID、版本號、codesign 完整性。
- 替換時先把舊版移到備份，新版開不起來就自動還原。
- 請求不帶任何使用者資料；User-Agent 只有程式名稱與版本。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import shutil
import ssl
import subprocess
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import ed25519
import version as V

MAX_ZIP_BYTES = 300 * 1024 * 1024
TIMEOUT = 20
CACHE_DIR = Path.home() / "Library" / "Caches" / "ZiHaiSwitch" / "update"
_ALLOWED_HOSTS = ("github.com", "api.github.com")
_ALLOWED_SUFFIX = ".githubusercontent.com"


def _ssl_context() -> ssl.SSLContext:
    """打包版用內建的 certifi 憑證（使用者電腦不一定有 Homebrew 的憑證路徑）。"""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str
    notes: str
    page_url: str
    zip_url: str | None
    sig_url: str | None


def arch() -> str:
    return "arm64" if platform.machine() == "arm64" else "x86_64"


def asset_name(ver: str) -> str:
    return f"ZiHaiSwitch-{ver}-macos-{arch()}.zip"


def signed_message(ver: str, arch_name: str, sha256_hex: str) -> bytes:
    return f"ZiHaiSwitch|{ver}|{arch_name}|{sha256_hex}".encode()


def configured() -> bool:
    return "OWNER" not in V.UPDATE_REPO and len(V.UPDATE_PUBLIC_KEY) == 64


# ---------------------------------------------------------------- 網路
def _check_url(url: str) -> None:
    u = urllib.parse.urlparse(url)
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not (host in _ALLOWED_HOSTS or host.endswith(_ALLOWED_SUFFIX)):
        raise UpdateError("更新來源不在允許的網域內")


def _get(url: str, limit: int, accept: str = "application/octet-stream") -> bytes:
    _check_url(url)
    req = urllib.request.Request(url, headers={
        "User-Agent": f"ZiHaiSwitch-Updater/{V.VERSION}",
        "Accept": accept,
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ssl_context()) as r:
            _check_url(r.geturl())  # 轉址後的最終網址也要在白名單內
            data = r.read(limit + 1)
    except UpdateError:
        raise
    except Exception as e:  # noqa: BLE001
        raise UpdateError("無法連線到 GitHub") from e
    if len(data) > limit:
        raise UpdateError("更新檔大小異常")
    return data


# ---------------------------------------------------------------- 檢查
def latest_release() -> Release:
    if "OWNER" in V.UPDATE_REPO:
        raise UpdateError("尚未設定更新來源")
    raw = _get(f"https://api.github.com/repos/{V.UPDATE_REPO}/releases/latest", 1024 * 1024,
               accept="application/vnd.github+json")
    try:
        info = json.loads(raw)
    except ValueError as e:
        raise UpdateError("更新資訊格式錯誤") from e
    ver = str(info.get("tag_name", "")).lstrip("vV")
    if V.parse_version(ver) is None:
        raise UpdateError("更新資訊格式錯誤")
    want = asset_name(ver)
    assets = {a.get("name"): a.get("browser_download_url") for a in info.get("assets", [])}
    return Release(
        version=ver,
        notes=str(info.get("body") or "").strip(),
        page_url=str(info.get("html_url") or f"https://github.com/{V.UPDATE_REPO}/releases"),
        zip_url=assets.get(want),
        sig_url=assets.get(want + ".sig"),
    )


def is_newer(ver: str) -> bool:
    a, b = V.parse_version(ver), V.parse_version(V.VERSION)
    return a is not None and b is not None and a > b


# ---------------------------------------------------------------- 下載與驗證
def download_verified(rel: Release) -> Path:
    if not configured():
        raise UpdateError("這個版本沒有內建更新簽章公鑰，請到 GitHub 手動下載")
    if not is_newer(rel.version):
        raise UpdateError("不是較新的版本")
    if not rel.zip_url or not rel.sig_url:
        raise UpdateError(f"新版本沒有提供 {arch()} 的安裝檔")
    sig_hex = _get(rel.sig_url, 4096).decode("ascii", "ignore").strip()
    data = _get(rel.zip_url, MAX_ZIP_BYTES)
    digest = hashlib.sha256(data).hexdigest()
    try:
        ok = ed25519.verify(bytes.fromhex(V.UPDATE_PUBLIC_KEY),
                            signed_message(rel.version, arch(), digest), bytes.fromhex(sig_hex))
    except ValueError:
        ok = False
    if not ok:
        raise UpdateError("更新檔簽章驗證失敗，已停止安裝")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="v" + rel.version + "-", dir=CACHE_DIR))
    zpath = work / "update.zip"
    zpath.write_bytes(data)
    out = work / "x"
    r = subprocess.run(["/usr/bin/ditto", "-x", "-k", str(zpath), str(out)], capture_output=True)
    if r.returncode != 0:
        raise UpdateError("更新檔解壓失敗")
    apps = [p for p in out.iterdir() if p.suffix == ".app"]
    if len(apps) != 1 or apps[0].is_symlink():
        raise UpdateError("更新檔內容異常")
    app = apps[0]
    try:
        info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    except (OSError, plistlib.InvalidFileException) as e:
        raise UpdateError("更新檔內容異常") from e
    if info.get("CFBundleIdentifier") != V.BUNDLE_ID or info.get("CFBundleShortVersionString") != rel.version:
        raise UpdateError("更新檔身分不符")
    r = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True)
    if r.returncode != 0:
        raise UpdateError("更新檔完整性檢查失敗")
    return app


# ---------------------------------------------------------------- 安裝
_INSTALL_SH = r"""#!/bin/sh
# $1 = 舊程式 PID，$2 = 新版 .app，$3 = 目前安裝位置
pid="$1"; new="$2"; dst="$3"; bak="$dst.previous"
i=0
while kill -0 "$pid" 2>/dev/null && [ $i -lt 100 ]; do sleep 0.2; i=$((i+1)); done
rm -rf "$bak"
if mv "$dst" "$bak" && mv "$new" "$dst"; then
  /usr/bin/xattr -dr com.apple.quarantine "$dst" 2>/dev/null
  if /usr/bin/open "$dst"; then rm -rf "$bak"; exit 0; fi
  rm -rf "$dst"
fi
# 失敗：還原舊版
[ -d "$bak" ] && mv "$bak" "$dst"
/usr/bin/open "$dst"
"""


def install_and_relaunch(new_app: Path, current_app: Path) -> None:
    """啟動背景安裝腳本；呼叫端接著要結束程式。"""
    if not os.access(current_app.parent, os.W_OK):
        raise UpdateError(f"沒有權限寫入 {current_app.parent}，請手動下載新版")
    script = new_app.parent.parent / "install.sh"
    script.write_text(_INSTALL_SH)
    os.chmod(script, 0o700)
    subprocess.Popen(["/bin/sh", str(script), str(os.getpid()), str(new_app), str(current_app)],
                     start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cleanup_cache() -> None:
    shutil.rmtree(CACHE_DIR, ignore_errors=True)
