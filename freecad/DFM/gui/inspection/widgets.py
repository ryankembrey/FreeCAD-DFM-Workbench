# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Widgets the Symmetry Check and Compare Bodies panels share.

Both are the results panel's own: the verdict banner (a tinted strip with
the status icon, the verdict in its color and the numbers) and a tree drawn
by :class:`DFMTreeDelegate`, with the blue info icon on each difference,
counts on the group rows, and Ignore / Restore on right-click.
"""

from __future__ import annotations

import html
from typing import Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtGui import QStandardItem, QStandardItemModel

from ..results.delegates import DFMTreeDelegate
from ..results.header import ElidedLabel

# Kinds of verdict: color and status icon, as in the results panel.
VERDICTS = {
    "pass": ("#639922", ":/icons/dfm_success.svg"),
    "warn": ("#D4900A", ":/icons/dfm_warning.svg"),
    "fail": ("#E24B4A", ":/icons/dfm_error.svg"),
    "info": ("#378ADD", ":/icons/dfm_info.svg"),
}

INFO_ICON = ":/icons/dfm_info.svg"
INFO_HEX = "#378ADD"

# Difference colors: amber for symmetry, orange added, blue removed, purple changed.
SYMMETRY_HEX = "#E3A32E"
ADDED_HEX = "#E8822A"
REMOVED_HEX = "#2F78C4"
CHANGED_HEX = "#8E6CC4"
NEUTRAL_HEX = "#888888"

_ROLE = QtCore.Qt.ItemDataRole.UserRole
ITEM_DATA = _ROLE           # the Difference, or the group's list of them
ITEM_TYPE = _ROLE + 1       # "rule" (a group row) or "finding" (a row)
PRIMARY = _ROLE + 2
SECONDARY = _ROLE + 3
COLOR = _ROLE + 4
IGNORED = _ROLE + 5
COUNT = _ROLE + 7           # the amber badge on a group row
IGNORED_COUNT = _ROLE + 8   # the grey one

_ROW_H = 28
_MAX_ROWS = 12


def hex_rgb(hex_color: str) -> tuple[float, float, float]:
    c = QtGui.QColor(hex_color)
    return (c.redF(), c.greenF(), c.blueF())


def info_icon(ignored: bool = False) -> QtGui.QIcon:
    """The blue info icon, or greyed for something ignored."""
    icon = QtGui.QIcon(INFO_ICON)
    if not ignored:
        return icon
    grey = QtGui.QIcon()
    for size in (16, 32):
        grey.addPixmap(icon.pixmap(size, size, QtGui.QIcon.Mode.Disabled))
    return grey


class VerdictBanner(QtWidgets.QFrame):
    """"⚠ Not symmetric   3 differences · 1.8 % of the part": the results
    panel's banner, without its model line."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dfmVerdictBanner")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(8, 6, 10, 6)
        row.setSpacing(8)
        self._icon = QtWidgets.QLabel()
        self._icon.setFixedSize(16, 16)
        row.addWidget(self._icon)
        self._word = QtWidgets.QLabel()
        self._word.setTextFormat(QtCore.Qt.TextFormat.RichText)
        font = self._word.font()
        font.setBold(True)
        self._word.setFont(font)
        row.addWidget(self._word)
        self._detail = ElidedLabel()
        row.addWidget(self._detail, 1)
        self.word = ""
        self.detail = ""
        self.kind = ""

    def set_verdict(self, kind: str, word: str, detail: str) -> None:
        color, icon = VERDICTS.get(kind, VERDICTS["info"])
        self._word.setText(f'<span style="color: {color}">{html.escape(word)}</span>')
        self._detail.setText(detail)
        ratio = self.devicePixelRatioF() or 1.0
        self._icon.setPixmap(QtGui.QIcon(icon).pixmap(QtCore.QSize(16, 16), ratio))
        c = QtGui.QColor(color)
        self.setStyleSheet(
            "QFrame#dfmVerdictBanner {"
            f" background: rgba({c.red()}, {c.green()}, {c.blue()}, 40);"
            f" border: none; border-left: 4px solid {color}; border-radius: 3px; }}"
            " QFrame#dfmVerdictBanner QLabel { background: transparent; }")
        self.setAccessibleName(f"{word}: {detail}")
        self.kind, self.word, self.detail = kind, word, detail


class Group:
    """A titled group of rows in the tree; *title* None puts the rows at the top level."""

    def __init__(self, title: Optional[str], color: str, rows: Sequence, counts: bool = True):
        self.title = title
        self.color = color
        #: (Difference, secondary text), or (None, label, text) for a plain row.
        self.rows = list(rows)
        #: Show how many it holds (Properties doesn't).
        self.counts = counts


class DifferenceTree(QtWidgets.QTreeView):
    """The differences, drawn as the results panel draws findings.

    ``selected(difference or None)`` when the current row changes,
    ``activated(difference)`` on double-click (zoom to it again),
    ``hovered(difference or None)``, ``zoom_requested(difference)`` and
    ``ignore_requested(differences, ignore)`` from the right-click menu.
    """

    selected = QtCore.Signal(object)
    activated = QtCore.Signal(object)
    hovered = QtCore.Signal(object)
    zoom_requested = QtCore.Signal(object)
    ignore_requested = QtCore.Signal(object, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.setExpandsOnDoubleClick(False)
        self.setMouseTracking(True)
        self.setItemDelegate(DFMTreeDelegate(self))
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self._model = QStandardItemModel(self)
        self.setModel(self._model)
        self._current = None
        self._hovered = None
        self.selectionModel().currentChanged.connect(self._on_current)
        self.entered.connect(self._on_entered)
        self.doubleClicked.connect(self._on_double)
        self.customContextMenuRequested.connect(self._on_menu)
        self.expanded.connect(lambda _i: self._fit_height())
        self.collapsed.connect(lambda _i: self._fit_height())
        self.viewport().installEventFilter(self)

    # -- content --------------------------------------------------------------------

    def set_groups(self, groups: Sequence[Group]) -> None:
        """Fill the tree, keeping which groups were collapsed and the current row."""
        collapsed = self._collapsed_titles()
        current = self._current
        self.selectionModel().blockSignals(True)
        self._model.clear()
        root = self._model.invisibleRootItem()
        grouped = any(g.title for g in groups)
        for group in groups:
            if not group.rows:
                continue
            parent = root
            if group.title:
                parent = self._group_item(group)
                root.appendRow(parent)
            for row in group.rows:
                parent.appendRow(self._row_item(row, group.color))
        self.setRootIsDecorated(grouped)
        self.expandAll()
        for r in range(root.rowCount()):
            item = root.child(r)
            if item.data(PRIMARY) in collapsed:
                self.collapse(item.index())
        self.selectionModel().blockSignals(False)
        self._current = None
        self.select(current)
        self._fit_height()

    def _group_item(self, group: Group) -> QStandardItem:
        diffs = [row[0] for row in group.rows if row[0] is not None]
        active = [d for d in diffs if not d.ignored]
        item = QStandardItem()
        item.setEditable(False)
        item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
        item.setData(diffs, ITEM_DATA)
        item.setData("rule", ITEM_TYPE)
        item.setData(group.title, PRIMARY)
        item.setData(group.color, COLOR)
        if group.counts:
            item.setData(len(active), COUNT)
            item.setData(len(diffs) - len(active), IGNORED_COUNT)
            item.setIcon(info_icon(ignored=bool(diffs) and not active))
        return item

    def _row_item(self, row, color: str) -> QStandardItem:
        item = QStandardItem()
        item.setEditable(False)
        item.setData("finding", ITEM_TYPE)
        item.setData(color, COLOR)
        if row[0] is None:  # a plain row, such as Volume in Properties
            _none, label, text = row
            item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            item.setData(label, PRIMARY)
            item.setData(text, SECONDARY)
            item.setToolTip(f"{label}: {text}")
            return item
        difference, text = row
        item.setData(difference, ITEM_DATA)
        item.setData(difference.name, PRIMARY)
        item.setData(text, SECONDARY)
        item.setData(difference.ignored, IGNORED)
        item.setIcon(info_icon(difference.ignored))
        item.setToolTip(f"{difference.name} · {text}\nClick to zoom to it. Right-click to "
                        f"{'restore' if difference.ignored else 'ignore'} it.")
        return item

    def _collapsed_titles(self) -> set:
        root = self._model.invisibleRootItem()
        out = set()
        for r in range(root.rowCount()):
            item = root.child(r)
            if item.hasChildren() and not self.isExpanded(item.index()):
                out.add(item.data(PRIMARY))
        return out

    def _items(self):
        def walk(parent):
            for r in range(parent.rowCount()):
                item = parent.child(r)
                yield item
                yield from walk(item)
        yield from walk(self._model.invisibleRootItem())

    def differences(self) -> list:
        return [item.data(ITEM_DATA) for item in self._items() if item.data(ITEM_TYPE) == "finding"
                and item.data(ITEM_DATA) is not None]

    def group_titles(self) -> list[str]:
        root = self._model.invisibleRootItem()
        return [root.child(r).data(PRIMARY) for r in range(root.rowCount()) if root.child(r).hasChildren()]

    def row_texts(self) -> list[tuple[str, str]]:
        return [(item.data(PRIMARY), item.data(SECONDARY)) for item in self._items()
                if item.data(ITEM_TYPE) == "finding"]

    # -- selection ------------------------------------------------------------------

    def select(self, difference) -> None:
        """Make *difference* the current row (None: no row), without signals."""
        self._current = difference
        sm = self.selectionModel()
        sm.blockSignals(True)
        try:
            item = next((i for i in self._items() if i.data(ITEM_DATA) is difference
                         and i.data(ITEM_TYPE) == "finding"), None) if difference is not None else None
            if item is None:
                sm.clearSelection()
                sm.setCurrentIndex(QtCore.QModelIndex(), QtCore.QItemSelectionModel.SelectionFlag.NoUpdate)
            else:
                sm.setCurrentIndex(item.index(), QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect)
                self.scrollTo(item.index())
        finally:
            sm.blockSignals(False)
        self.viewport().update()

    def current(self):
        return self._current

    def _difference_at(self, index: QtCore.QModelIndex):
        if not index.isValid() or index.data(ITEM_TYPE) != "finding":
            return None
        return index.data(ITEM_DATA)

    def _on_current(self, index, _previous) -> None:
        difference = self._difference_at(index)
        if difference is None and index.isValid():
            return  # a group or property row: keep the selection as it is
        if difference is not self._current:
            self._current = difference
            self.selected.emit(difference)

    def _on_double(self, index) -> None:
        difference = self._difference_at(index)
        if difference is not None:
            self.activated.emit(difference)

    def _on_entered(self, index) -> None:
        self._set_hovered(self._difference_at(index))

    def _set_hovered(self, difference) -> None:
        if difference is not self._hovered:
            self._hovered = difference
            self.hovered.emit(difference)

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.viewport() and event.type() == QtCore.QEvent.Type.Leave:
            self._set_hovered(None)
        return super().eventFilter(obj, event)

    # -- right-click ----------------------------------------------------------------

    def _on_menu(self, point: QtCore.QPoint) -> None:
        index = self.indexAt(point)
        if not index.isValid():
            return
        menu = QtWidgets.QMenu(self)
        difference = self._difference_at(index)
        if difference is not None:
            zoom = menu.addAction("Zoom to it")
            zoom.triggered.connect(lambda _c=False, d=difference: self.zoom_requested.emit(d))
            menu.addSeparator()
            ignore = menu.addAction("Restore" if difference.ignored else "Ignore")
            ignore.setToolTip("An ignored difference is greyed and doesn’t count against the verdict.")
            ignore.triggered.connect(
                lambda _c=False, d=difference: self.ignore_requested.emit([d], not d.ignored))
        elif index.data(ITEM_TYPE) == "rule":
            diffs = index.data(ITEM_DATA) or []
            active = [d for d in diffs if not d.ignored]
            ignored = [d for d in diffs if d.ignored]
            if active:
                act = menu.addAction(f"Ignore All ({len(active)})")
                act.triggered.connect(lambda _c=False, a=active: self.ignore_requested.emit(a, True))
            if ignored:
                act = menu.addAction(f"Restore All ({len(ignored)})")
                act.triggered.connect(lambda _c=False, i=ignored: self.ignore_requested.emit(i, False))
        if not menu.isEmpty():
            menu.exec(self.viewport().mapToGlobal(point))

    # -- size -------------------------------------------------------------------------

    def _visible_rows(self) -> int:
        count = 0
        index = self._model.index(0, 0) if self._model.rowCount() else QtCore.QModelIndex()
        while index.isValid():
            count += 1
            index = self.indexBelow(index)
        return count

    def _fit_height(self) -> None:
        """As tall as its rows, up to a few; then it scrolls."""
        rows = max(1, min(self._visible_rows(), _MAX_ROWS))
        self.setFixedHeight(rows * _ROW_H + 2 * self.frameWidth() + 2)
