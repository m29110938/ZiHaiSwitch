"""「從錄音建立音效」視窗：匯入一段打字錄音 → 自動切片 → 每聲指定用途、邊改邊聽 → 存成一組音效，可再編輯。"""

from __future__ import annotations

from pathlib import Path

import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSButton,
    NSOpenPanel,
    NSPopUpButton,
    NSPopUpButtonCell,
    NSScrollView,
    NSTableColumn,
    NSTableView,
    NSTextField,
    NSWindow,
)
from Foundation import NSMakeRect, NSObject

import library
import sampler
import soundbank as sg
import ui

WIN_W, WIN_H = 520, 460
_MODAL_OK = 1
ROLE_LABELS = {
    "normal": "一般鍵・按下",
    "normal_up": "一般鍵・放開",
    "space": "空白鍵",
    "enter": "Enter",
    "backspace": "Backspace",
    "modifier": "修飾鍵",
}


class RecordController(NSObject):
    def initWithContext_(self, ctx):
        self = objc.super(RecordController, self).init()
        if self is None:
            return None
        self.ctx = ctx
        self.audio = None
        self.source = None
        self.events = []
        self.top_db = 0.0
        self.window = None
        self.editing = None   # 正在編輯的音效 ID；None = 新建
        return self

    @objc.python_method
    def build(self):
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIN_W, WIN_H), 1 | 2 | 4, NSBackingStoreBuffered, False)
        win.setTitle_("從錄音建立音效")
        win.setReleasedWhenClosed_(False)
        win.setDelegate_(self)
        content = win.contentView()

        def button(title, x, y, w, action):
            b = NSButton.buttonWithTitle_target_action_(title, self, action)
            b.setFrame_(NSMakeRect(x, y, w, 30))
            content.addSubview_(b)
            return b

        button("選擇錄音…", 20, WIN_H - 48, 120, "chooseFile:")
        mine = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(WIN_W - 20 - 80 - 8 - 200, WIN_H - 46, 200, 26), False)
        mine.setTarget_(self)
        mine.setAction_("pickMine:")
        content.addSubview_(mine)
        self.mine_popup = mine
        self.delete_btn = button("刪除", WIN_W - 20 - 80, WIN_H - 48, 80, "deleteMine:")

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(20, 64, WIN_W - 40, WIN_H - 124))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(2)
        table = NSTableView.alloc().initWithFrame_(scroll.bounds())

        role_cell = NSPopUpButtonCell.alloc().initTextCell_pullsDown_("", False)
        role_cell.addItemsWithTitles_(["不使用"] + [ROLE_LABELS[r] for r in sampler.ROLES])
        role_cell.setBordered_(False)
        for ident, title, width, cell in (
            ("time", "時間", 100, None),
            ("role", "用途", 230, role_cell),
            ("level", "響度", 100, None),
        ):
            col = NSTableColumn.alloc().initWithIdentifier_(ident)
            col.headerCell().setStringValue_(title)
            col.setWidth_(width)
            col.setEditable_(cell is not None)
            if cell is not None:
                col.setDataCell_(cell)
            table.addTableColumn_(col)
        table.setDataSource_(self)
        table.setDelegate_(self)
        table.setTarget_(self)
        table.setDoubleAction_("preview:")
        table.setUsesAlternatingRowBackgroundColors_(True)
        table.setRowHeight_(20)
        scroll.setDocumentView_(table)
        content.addSubview_(scroll)
        self.table = table

        self.preview_btn = button("試聽", 20, 18, 90, "preview:")
        name = NSTextField.alloc().initWithFrame_(NSMakeRect(WIN_W - 20 - 110 - 10 - 200, 22, 200, 24))
        name.setPlaceholderString_("名稱")
        content.addSubview_(name)
        self.name_field = name
        self.create_btn = button("建立", WIN_W - 20 - 110, 18, 110, "create:")
        self.window = win
        self.refresh()
        win.center()

    @objc.python_method
    def refresh(self):
        self.table.reloadData()
        has = bool(self.events)
        self.preview_btn.setEnabled_(has)
        self.create_btn.setEnabled_(has or self.editing is not None)
        self.create_btn.setTitle_("儲存" if self.editing else "建立")
        self.name_field.setEnabled_(has or self.editing is not None)
        self.delete_btn.setEnabled_(self.editing is not None)
        # 我的音效：第一項是「新增」，其餘是自建音效
        self.mine = sampler.user_profiles()
        self.mine_popup.removeAllItems()
        self.mine_popup.addItemWithTitle_("新增")
        used = {"新增"}
        for _, label in self.mine:
            title, n = label, 2
            while title in used:  # NSPopUpButton 同名項目會互相覆蓋
                title, n = f"{label} {n}", n + 1
            used.add(title)
            self.mine_popup.addItemWithTitle_(title)
        ids = [sid for sid, _ in self.mine]
        self.mine_popup.selectItemAtIndex_(ids.index(self.editing) + 1 if self.editing in ids else 0)

    @objc.python_method
    def update_trial(self):
        """編輯中的音效立刻套用到打字聲；沒有可用的一般鍵就還原。"""
        bank = None
        if self.audio is not None and self.events:
            try:
                bank = sampler.bank_from_selection(self.audio, self.events)
            except ValueError:
                bank = None
        self.ctx["on_trial"](bank)

    def windowWillClose_(self, note):
        self.ctx["on_trial"](None)  # 關閉視窗就恢復原本的音效

    @objc.python_method
    def reset(self):
        self.ctx["on_trial"](None)
        self.audio, self.source, self.events, self.editing = None, None, [], None
        self.name_field.setStringValue_("")
        self.window.setTitle_("從錄音建立音效")
        self.refresh()

    @objc.python_method
    def load(self, x, events, source, title):
        self.audio, self.source, self.events = x, source, events
        self.top_db = max(e["peak_db"] for e in events)
        self.window.setTitle_(f"從錄音建立音效 — {title}")
        self.update_trial()

    @objc.python_method
    def alert(self, title, text=""):
        ui.alert(title, text).runModal()

    # ------------------------------------------------------------ NSTableView data source
    def numberOfRowsInTableView_(self, table):
        return len(self.events)

    def tableView_objectValueForTableColumn_row_(self, table, col, row):
        e = self.events[row]
        ident = col.identifier()
        if ident == "time":
            return f"{e['start'] / sg.SAMPLE_RATE:.2f} 秒"
        if ident == "role":
            return sampler.ROLES.index(e["role"]) + 1 if e["use"] else 0
        return f"{e['peak_db'] - self.top_db:.0f} dB"

    def tableView_setObjectValue_forTableColumn_row_(self, table, value, col, row):
        if not (0 <= row < len(self.events)):
            return
        e = self.events[row]
        ident = col.identifier()
        if ident != "role":
            return
        i = int(value)
        if i == 0:
            e["use"] = False
        elif 1 <= i <= len(sampler.ROLES):
            e["role"] = sampler.ROLES[i - 1]
            e["use"] = True
            self.play_row(row)
        self.update_trial()

    # ------------------------------------------------------------ 動作
    @objc.python_method
    def play_row(self, row):
        if self.audio is not None and 0 <= row < len(self.events):
            self.ctx["play"](sampler.preview_sample(self.audio, self.events, self.events[row]))

    def preview_(self, sender):
        row = self.table.selectedRow()
        if row < 0 and self.events:
            row = 0
            self.table.selectRowIndexes_byExtendingSelection_(
                objc.lookUpClass("NSIndexSet").indexSetWithIndex_(0), False)
        self.play_row(row)

    def chooseFile_(self, sender):
        panel = NSOpenPanel.openPanel()
        panel.setAllowsMultipleSelection_(False)
        panel.setCanChooseDirectories_(False)
        panel.setAllowedFileTypes_(["wav", "aif", "aiff", "aifc", "mp3", "m4a", "caf", "flac", "mp4", "mov"])
        if panel.runModal() != _MODAL_OK:
            return
        path = panel.URLs()[0].path()
        try:
            x = sampler.read_audio(path)[: sampler.MAX_SECONDS * sg.SAMPLE_RATE]
            events = sampler.analyze(x)
        except (ValueError, OSError):
            self.alert("無法讀取這個音檔")
            return
        if not events:
            self.alert("找不到按鍵聲", "請換一段比較安靜、按鍵聲清楚的錄音")
            return
        self.load(x, events, path, Path(path).name)
        if self.editing is None:
            self.name_field.setStringValue_(library.default_name(Path(path).stem))
        self.refresh()

    def pickMine_(self, sender):
        i = int(sender.indexOfSelectedItem())
        if i <= 0 or i > len(self.mine):
            self.reset()
            return
        sid, label = self.mine[i - 1]
        self.reset()
        self.editing = sid
        self.name_field.setStringValue_(label)
        data = sampler.load_edit(sid)
        if data is not None:
            x, events, source = data
            if events:
                self.load(x, events, source, label)
        self.refresh()

    def deleteMine_(self, sender):
        if self.editing is None:
            return
        label = dict(self.mine).get(self.editing, "")
        a = ui.alert(f"刪除「{label}」？", "刪除後無法復原", ("刪除", "取消"))
        if int(a.runModal()) != 1000:
            return
        sid = self.editing
        try:
            sampler.delete_user_profile(sid)
        except OSError:
            self.alert("無法刪除")
            return
        self.reset()
        self.ctx["on_deleted"](sid)

    def create_(self, sender):
        label = str(self.name_field.stringValue() or "").strip()
        if self.audio is None:
            # 舊資料沒有保留錄音：只能改名
            if self.editing and label:
                sampler.rename_user_profile(self.editing, label)
                self.ctx["on_changed"]()
                self.refresh()
            return
        label = label or library.default_name(Path(self.source).stem)
        try:
            sid = sampler.save_user_profile(label, self.audio, self.events, self.source, sid=self.editing)
        except ValueError as e:
            self.alert(str(e))
            return
        except OSError:
            self.alert("無法儲存音效")
            return
        sampler.load_profiles()
        self.ctx["on_created"](sid)
        self.reset()
        self.window.orderOut_(None)


_controller = None


def open_record(play, on_trial=lambda bank: None, on_created=lambda sid: None, on_deleted=lambda sid: None, on_changed=lambda: None):
    global _controller
    if _controller is None:
        _controller = RecordController.alloc().initWithContext_(
            {"play": play, "on_trial": on_trial, "on_created": on_created, "on_deleted": on_deleted, "on_changed": on_changed})
        _controller.build()
    elif not _controller.events and _controller.editing is None:
        _controller.refresh()
    else:
        _controller.update_trial()  # 重新打開視窗時，繼續用編輯中的音效
    NSApp.activateIgnoringOtherApps_(True)
    _controller.window.makeKeyAndOrderFront_(None)
    return _controller
