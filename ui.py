"""共用 UI 小工具。"""

from __future__ import annotations

from AppKit import NSAlert, NSImage

from paths import ASSETS

_icon = None


def app_icon():
    """程式圖示（8-bit 鍵帽）。從原始碼執行時系統預設會顯示 Python 圖示，所以一律明確指定。"""
    global _icon
    if _icon is None:
        _icon = NSImage.alloc().initWithContentsOfFile_(str(ASSETS / "icons" / "AppIcon.icns"))
    return _icon


def alert(title: str, text: str = "", buttons: tuple[str, ...] = ()) -> NSAlert:
    """建立帶程式圖示的提示框；呼叫端自行 runModal()，第一個按鈕回傳 1000。"""
    a = NSAlert.alloc().init()
    a.setMessageText_(title)
    a.setInformativeText_(text)
    for b in buttons:
        a.addButtonWithTitle_(b)
    icon = app_icon()
    if icon is not None:
        a.setIcon_(icon)
    return a
