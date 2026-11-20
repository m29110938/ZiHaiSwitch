"""「自訂按鍵」視窗：點鍵盤上的鍵，設定它按下／放開的音效，並可匯入音效檔。"""

from __future__ import annotations

import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMenuItem,
    NSParagraphStyleAttributeName,
    NSMutableParagraphStyle,
    NSEvent,
    NSPopUpButton,
    NSScrollView,
    NSTextField,
    NSTextView,
    NSTimer,
    NSWindow,
)
from Foundation import NSAttributedString, NSMakeRect, NSObject

import keymap
import ui

UNIT = 44
KEY_H = 36
GAP = 4
MARGIN = 20
PANEL_H = 100
TEST_H = 96
KB_W = 15 * UNIT
WIN_W = KB_W + MARGIN * 2
TOP_H = 44
WIN_H = TOP_H + MARGIN + len(keymap.ROWS) * (KEY_H + GAP) + MARGIN + PANEL_H + TEST_H

_BUTTON_PUSH_ON_OFF = 1
_BEZEL_SMALL_SQUARE = 10
_MODAL_OK = 1


class SettingsController(NSObject):
    def initWithContext_(self, ctx):
        self = objc.super(SettingsController, self).init()
        if self is None:
            return None
        self.ctx = ctx
        self.selected = None
        self.buttons = {}
        self.window = None
        return self

    # ------------------------------------------------------------ 建立視窗
    @objc.python_method
    def build(self):
        style = 1 | 2 | 4  # titled | closable | miniaturizable
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIN_W, WIN_H), style, NSBackingStoreBuffered, False)
        win.setTitle_("自訂按鍵")
        win.setReleasedWhenClosed_(False)
        content = win.contentView()

        for r, row in enumerate(keymap.ROWS):
            y = WIN_H - TOP_H - MARGIN - (r + 1) * (KEY_H + GAP) + GAP
            x = MARGIN
            for kc, name, units in row:
                w = units * UNIT
                if kc is not None:
                    b = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w - GAP, KEY_H))
                    b.setButtonType_(_BUTTON_PUSH_ON_OFF)
                    b.setBezelStyle_(_BEZEL_SMALL_SQUARE)
                    b.setTag_(kc)
                    b.setTarget_(self)
                    b.setAction_("keyClicked:")
                    content.addSubview_(b)
                    self.buttons[kc] = (b, name)
                x += w

        def label(text, x, y, w, bold=False):
            t = NSTextField.labelWithString_(text)
            t.setFrame_(NSMakeRect(x, y, w, 20))
            if bold:
                t.setFont_(NSFont.boldSystemFontOfSize_(15))
            content.addSubview_(t)
            return t

        def button(title, x, y, w, action):
            b = NSButton.buttonWithTitle_target_action_(title, self, action)
            b.setFrame_(NSMakeRect(x, y, w, 30))
            content.addSubview_(b)
            return b

        # 全部按鍵：一鍵切換整組音效（= 選單「音效」）
        label("全部按鍵", MARGIN, WIN_H - MARGIN - 24, 70)
        self.all_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(MARGIN + 72, WIN_H - MARGIN - 30, 230, 28), False)
        self.all_popup.setTarget_(self)
        self.all_popup.setAction_("allChanged:")
        content.addSubview_(self.all_popup)

        py = MARGIN + TEST_H
        self.key_label = label("", MARGIN, py + 42, 150, bold=True)
        label("按下", 180, py + 44, 40)
        label("放開", 180, py + 6, 40)
        self.down_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(220, py + 38, 230, 28), False)
        self.up_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(220, py, 230, 28), False)
        for p, act in ((self.down_popup, "downChanged:"), (self.up_popup, "upChanged:")):
            p.setTarget_(self)
            p.setAction_(act)
            content.addSubview_(p)
        self.preview_btn = button("試聽", 460, py + 37, 80, "preview:")
        self.reset_btn = button("重設", 460, py - 1, 80, "resetKey:")

        # 試打區：直接打字感受整體音效（按下的鍵會在鍵盤上亮起）
        label("試打", MARGIN, MARGIN + 70, 60)
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(MARGIN, MARGIN, KB_W, 64))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(2)
        tv = NSTextView.alloc().initWithFrame_(scroll.contentView().bounds())
        tv.setFont_(NSFont.systemFontOfSize_(14))
        tv.setRichText_(False)
        tv.setAutoresizingMask_(2)  # width sizable
        scroll.setDocumentView_(tv)
        content.addSubview_(scroll)
        self.test_view = tv
        self._monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            (1 << 10) | (1 << 11) | (1 << 12), self.flash_key)

        self.window = win
        self.populate_popups()
        self.refresh_all_keys()
        self.select(None)
        win.center()

    @objc.python_method
    def populate_all_popup(self):
        import soundbank as sg

        p = self.all_popup
        p.removeAllItems()
        menu = p.menu()
        builtin = [(k, v) for k, v in sg.PROFILES.items() if not k.startswith("user:")]
        user = [(k, v) for k, v in sg.PROFILES.items() if k.startswith("user:")]
        for gi, group in enumerate((builtin, user)):
            if gi and group:
                menu.addItem_(NSMenuItem.separatorItem())
            for sid, title in group:
                item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
                item.setRepresentedObject_(sid)
                menu.addItem_(item)
        self.select_popup(p, self.ctx["cfg"]["profile"])

    @objc.python_method
    def populate_popups(self):
        self.populate_all_popup()
        lib = self.ctx["lib"]
        for p in (self.down_popup, self.up_popup):
            p.removeAllItems()
            menu = p.menu()
            groups = [[("default", "預設"), ("none", "無聲")], lib.builtin_options(), lib.user_options()]
            for gi, group in enumerate(groups):
                if not group:
                    continue
                if gi:
                    menu.addItem_(NSMenuItem.separatorItem())
                for sid, title in group:
                    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
                    item.setRepresentedObject_(sid)
                    menu.addItem_(item)

    # ------------------------------------------------------------ 狀態同步
    @objc.python_method
    def override(self, kc):
        return self.ctx["cfg"]["keys"].get(str(kc), {})

    @objc.python_method
    def refresh_key(self, kc):
        b, name = self.buttons[kc]
        custom = bool(self.override(kc))
        style = NSMutableParagraphStyle.alloc().init()
        style.setAlignment_(1)  # center
        attrs = {
            NSFontAttributeName: NSFont.boldSystemFontOfSize_(12) if custom else NSFont.systemFontOfSize_(12),
            NSForegroundColorAttributeName: NSColor.systemOrangeColor() if custom else NSColor.labelColor(),
            NSParagraphStyleAttributeName: style,
        }
        b.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(name or " ", attrs))
        b.setState_(1 if kc == self.selected else 0)

    @objc.python_method
    def refresh_all_keys(self):
        for kc in self.buttons:
            self.refresh_key(kc)

    @objc.python_method
    def select_popup(self, popup, sid):
        for i in range(popup.numberOfItems()):
            if popup.itemAtIndex_(i).representedObject() == sid:
                popup.selectItemAtIndex_(i)
                return
        popup.selectItemAtIndex_(0)

    @objc.python_method
    def select(self, kc):
        prev, self.selected = self.selected, kc
        if prev in self.buttons:
            self.refresh_key(prev)
        enabled = kc is not None
        for c in (self.down_popup, self.up_popup, self.preview_btn, self.reset_btn):
            c.setEnabled_(enabled)
        if not enabled:
            self.key_label.setStringValue_("")
            return
        self.refresh_key(kc)
        self.key_label.setStringValue_(keymap.NAMES.get(kc, str(kc)))
        ov = self.override(kc)
        self.select_popup(self.down_popup, ov.get("down", "default"))
        self.select_popup(self.up_popup, ov.get("up", "default"))

    @objc.python_method
    def set_sound(self, which, sid):
        if self.selected is None:
            return
        keys = self.ctx["cfg"]["keys"]
        k = str(self.selected)
        ov = dict(keys.get(k, {}))
        ov[which] = sid
        if all(ov.get(w, "default") == "default" for w in ("down", "up")):
            keys.pop(k, None)
        else:
            keys[k] = ov
        self.ctx["save"]()
        self.refresh_key(self.selected)

    # ------------------------------------------------------------ 動作
    def allChanged_(self, sender):
        cfg = self.ctx["cfg"]
        sid = sender.selectedItem().representedObject()
        n = len(cfg["keys"])
        if n:
            alert = ui.alert("全部按鍵改用「%s」" % sender.selectedItem().title(),
                             f"會清除 {n} 個按鍵的個別設定", ("套用", "取消"))
            if alert.runModal() != 1000:
                self.select_popup(sender, cfg["profile"])
                return
            cfg["keys"].clear()
        cfg["profile"] = sid
        self.ctx["save"]()
        self.ctx["on_profile"]()
        self.refresh_all_keys()
        self.select(self.selected)

    def keyClicked_(self, sender):
        self.select(int(sender.tag()))

    def downChanged_(self, sender):
        self.set_sound("down", sender.selectedItem().representedObject())

    def upChanged_(self, sender):
        self.set_sound("up", sender.selectedItem().representedObject())

    def preview_(self, sender):
        if self.selected is not None:
            self.ctx["preview"](self.selected)

    def resetKey_(self, sender):
        if self.selected is None:
            return
        self.ctx["cfg"]["keys"].pop(str(self.selected), None)
        self.ctx["save"]()
        self.select(self.selected)

    @objc.python_method
    def flash_key(self, event):
        """試打時讓鍵盤上對應的鍵亮起來（只在本視窗內，不記錄）。"""
        if event.window() is not None and event.window() == self.window:
            kc = int(event.keyCode())
            if kc in self.buttons:
                b = self.buttons[kc][0]
                t = int(event.type())
                if t == 10:
                    b.highlight_(True)
                elif t == 11:
                    b.highlight_(False)
                else:  # 修飾鍵：短暫亮一下
                    b.highlight_(True)
                    NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                        0.15, False, lambda _t: b.highlight_(False))
        return event


_controller = None


def open_settings(cfg, lib, save, preview, on_profile=lambda: None):
    global _controller
    if _controller is None:
        _controller = SettingsController.alloc().initWithContext_(
            {"cfg": cfg, "lib": lib, "save": save, "preview": preview, "on_profile": on_profile})
        _controller.build()
    NSApp.activateIgnoringOtherApps_(True)
    _controller.window.makeKeyAndOrderFront_(None)
    _controller.window.makeFirstResponder_(_controller.test_view)
    return _controller


def refresh_key_window():
    """匯入新音效後，更新自訂按鍵視窗的下拉選單。"""
    if _controller is not None:
        _controller.populate_popups()
        _controller.select(_controller.selected)
