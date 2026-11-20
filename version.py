"""版本與更新設定。發新版時只改 VERSION，並在 CHANGELOG.md 新增一段。"""

APP_NAME = "自嗨軸 ZiHaiSwitch"
VERSION = "1.0.0"
BUNDLE_ID = "com.zihaiswitch.app"

# GitHub 上放 Releases 的 repo（帳號/名稱）。建立 repo 後改這一行。
UPDATE_REPO = "m29110938/ZiHaiSwitch"

# 更新檔簽章公鑰（Ed25519，hex）。由 `tools/release.py keygen` 產生；私鑰只存在發版者電腦。
UPDATE_PUBLIC_KEY = "6c37492afa390153657aa8d4a9b30cb43089434fb8e2797c8f42daa8b31498c2"


def parse_version(text: str) -> tuple[int, int, int] | None:
    t = text.strip().lstrip("vV")
    parts = t.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)  # type: ignore[return-value]
