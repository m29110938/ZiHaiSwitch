"""「匯入音效」視窗：匯入音效檔、在列表中直接改名、試聽。"""

from __future__ import annotations

import random

import objc
from AppKit import (
    NSAlert,
    NSApp,
    NSBackingStoreBuffered,
    NSButton,
    NSOpenPanel,
    NSScrollView,
    NSTableColumn,
    NSTableView,
    NSWindow,
)
from Foundation import NSMakeRect, NSObject

import library
import soundbank as sg

WIN_W, WIN_H = 420, 340
_MODAL_OK = 1


class ImportController(NSObject):
    def initWithContext_(self, ctx):
        self = objc.super(ImportController, self).init()
        if self is None:
            return None
        self.ctx = ctx
        self.rows = []
        self.window = None
        return self

    @objc.python_method
    def build(self):
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIN_W, WIN_H), 1 | 2 | 4, NSBackingStoreBuffered, False)
        win.setTitle_("匯入音效")
        win.setReleasedWhenClosed_(False)
        content = win.contentView()

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(20, 64, WIN_W - 40, WIN_H - 84))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(2)  # bezel
        table = NSTableView.alloc().initWithFrame_(scroll.bounds())
        for ident, title, width in (("name", "音效", 280), ("len", "長度", 70)):
            col = NSTableColumn.alloc().initWithIdentifier_(ident)
            col.headerCell().setStringValue_(title)
            col.setWidth_(width)
            col.setEditable_(ident == "name")  # 名稱直接在列表中改
            table.addTableColumn_(col)
        table.setDataSource_(self)
        table.setDelegate_(self)
        table.setTarget_(self)
        table.setDoubleAction_("preview:")
        table.setUsesAlternatingRowBackgroundColors_(True)
        scroll.setDocumentView_(table)
        content.addSubview_(scroll)
        self.table = table

        def button(title, x, w, action):
            b = NSButton.buttonWithTitle_target_action_(title, self, action)
            b.setFrame_(NSMakeRect(x, 18, w, 30))
            content.addSubview_(b)
            return b

        self.preview_btn = button("試聽", 20, 90, "preview:")
        self.delete_btn = button("刪除", 20 + 90 + 8, 90, "deleteSound:")
        button("匯入…", WIN_W - 20 - 110, 110, "importSound:")
        self.window = win
        self.reload()
        win.center()

    @objc.python_method
    def reload(self):
        lib = self.ctx["lib"]
        self.rows = [(sid, label, lib.samples(sid, False)) for sid, label in lib.user_options()]
        self.table.reloadData()
        self.preview_btn.setEnabled_(bool(self.rows))
        self.delete_btn.setEnabled_(bool(self.rows))

    # ------------------------------------------------------------ NSTableView data source
    def numberOfRowsInTableView_(self, table):
        return len(self.rows)

    def tableView_objectValueForTableColumn_row_(self, table, col, row):
        sid, label, samples = self.rows[row]
        if col.identifier() == "name":
            return label
        n = len(samples[0]) if samples else 0
        return f"{n / sg.SAMPLE_RATE:.2f} 秒"

    def tableView_setObjectValue_forTableColumn_row_(self, table, value, col, row):
        if col.identifier() != "name" or not (0 <= row < len(self.rows)):
            return
        sid, label, _ = self.rows[row]
        name = str(value or "").strip()
        if not name or name == label:
            return
        library.set_name(sid, name)
        self.ctx["lib"].reload_user()
        self.reload()
        self.select_sid(sid)
        self.ctx["on_changed"]()

    @objc.python_method
    def edit_name(self, sid):
        """選取並直接進入名稱編輯狀態。"""
        ids = [r[0] for r in self.rows]
        if sid in ids:
            row = ids.index(sid)
            self.select_sid(sid)
            self.table.scrollRowToVisible_(row)
            self.window.makeFirstResponder_(self.table)
            self.table.editColumn_row_withEvent_select_(0, row, None, True)

    # ------------------------------------------------------------ 動作
    def preview_(self, sender):
        row = self.table.selectedRow()
        if row < 0 and self.rows:
            row = 0
            self.table.selectRowIndexes_byExtendingSelection_(
                objc.lookUpClass("NSIndexSet").indexSetWithIndex_(0), False)
        if 0 <= row < len(self.rows) and self.rows[row][2]:
            self.ctx["play"](random.choice(self.rows[row][2]))

    @objc.python_method
    def selected_index(self):
        row = self.table.selectedRow()
        return row if 0 <= row < len(self.rows) else (0 if self.rows else -1)

    @objc.python_method
    def select_sid(self, sid):
        ids = [r[0] for r in self.rows]
        if sid in ids:
            self.table.selectRowIndexes_byExtendingSelection_(
                objc.lookUpClass("NSIndexSet").indexSetWithIndex_(ids.index(sid)), False)

    def deleteSound_(self, sender):
        row = self.table.selectedRow()
        if not (0 <= row < len(self.rows)):
            return
        sid, label, _ = self.rows[row]
        alert = NSAlert.alloc().init()
        alert.setMessageText_(f"刪除「{label}」？")
        alert.setInformativeText_("刪除後無法復原")
        alert.addButtonWithTitle_("刪除")
        alert.addButtonWithTitle_("取消")
        if int(alert.runModal()) != 1000:
            return
        try:
            library.delete_user_sound(sid)
        except OSError:
            return
        self.ctx["lib"].reload_user()
        self.reload()
        self.ctx["on_deleted"](sid)

    def importSound_(self, sender):
        panel = NSOpenPanel.openPanel()
        panel.setAllowsMultipleSelection_(True)
        panel.setCanChooseDirectories_(False)
        panel.setAllowedFileTypes_(["wav", "aif", "aiff", "aifc", "mp3", "m4a", "caf", "flac", "mp4"])
        if panel.runModal() != _MODAL_OK:
            return
        failed, added = [], []
        for url in panel.URLs():
            try:
                added.append(library.import_sound(url.path()))
            except (ValueError, OSError):
                failed.append(url.lastPathComponent())
        self.ctx["lib"].reload_user()
        self.reload()
        if added:
            self.ctx["on_changed"]()
            self.edit_name(added[-1])  # 匯入後直接在列表中命名
        if failed:
            alert = NSAlert.alloc().init()
            alert.setMessageText_("無法匯入")
            alert.setInformativeText_("\n".join(failed))
            alert.runModal()


_controller = None


def open_import(lib, play, on_changed=lambda: None, on_deleted=lambda sid: None):
    global _controller
    if _controller is None:
        _controller = ImportController.alloc().initWithContext_(
            {"lib": lib, "play": play, "on_changed": on_changed, "on_deleted": on_deleted})
        _controller.build()
    else:
        _controller.reload()
    NSApp.activateIgnoringOtherApps_(True)
    _controller.window.makeKeyAndOrderFront_(None)
    return _controller
