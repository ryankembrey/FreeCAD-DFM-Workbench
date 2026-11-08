# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The bench's feature tree, styled like the results panel's findings tree.

Rows are 28 px and painted by a delegate, as in the results tree:

* **Group rows** have a swatch in the kind's colour, the name, and a count
  badge. A red badge counts the features marked wrong.
* **Feature rows** have a dot in the kind's colour, then the feature, a
  middle dot, and its key numbers. At the right end are the review mark
  (a tick or a cross) and a confidence badge: green at 0.8 and above, amber
  above the reporting threshold, red below it.
* **Face rows** (under "No feature") are muted: the face, its surface and
  its area.

The selected row leaves room at its right end for the camera buttons.
"""

from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtGui import QStandardItem, QStandardItemModel

from ...core.recognition import FeatureKind
from .scene import UNOWNED_COLOR, kind_color

R_TYPE = QtCore.Qt.ItemDataRole.UserRole + 1      # group | feature | unowned | face
R_PRIMARY = QtCore.Qt.ItemDataRole.UserRole + 2
R_SECONDARY = QtCore.Qt.ItemDataRole.UserRole + 3
R_COLOR = QtCore.Qt.ItemDataRole.UserRole + 4
R_FID = QtCore.Qt.ItemDataRole.UserRole + 5
R_CONF = QtCore.Qt.ItemDataRole.UserRole + 6
R_VERDICT = QtCore.Qt.ItemDataRole.UserRole + 7
R_COUNT = QtCore.Qt.ItemDataRole.UserRole + 8
R_WRONG = QtCore.Qt.ItemDataRole.UserRole + 9
R_KIND = QtCore.Qt.ItemDataRole.UserRole + 10
R_FACE = QtCore.Qt.ItemDataRole.UserRole + 11
R_HISTORY = QtCore.Qt.ItemDataRole.UserRole + 12

GROUP_BY_KIND = "kind"
GROUP_BY_REVIEW = "review"
GROUP_FLAT = "flat"
GROUP_MODES = ((GROUP_BY_KIND, "By kind"), (GROUP_BY_REVIEW, "By review"), (GROUP_FLAT, "Flat"))

C_PASS = "#639922"
C_WARNING = "#D4900A"
C_ERROR = "#E24B4A"
C_MUTED = "#888888"

# Kinds in the order the engine reports them, so the tree reads the same
# way every time.
_KIND_ORDER = {k: i for i, k in enumerate(FeatureKind)}
_PLURAL = {"boss": "Bosses", "draft": "Drafted walls", "snap_fit": "Snap fits", "living_hinge": "Living hinges",
           "vent": "Vents", "lip": "Lips", "text": "Text", "overhang": "Overhangs", "bridge": "Bridges"}


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.4g}"
    if isinstance(value, (tuple, list)):
        return "(" + ", ".join(_fmt(v) for v in value) + ")"
    return str(value)


def summary(f) -> str:
    """The subtype and the two or three numbers that say most about *f*."""
    p = f.params
    bits = [f.subtype] if f.subtype else []
    for key, fmt in (("diameter", "Ø{}"), ("outer_diameter", "Ø{}"), ("radius", "R{}"),
                     ("width", "w {}"), ("thickness", "t {}"), ("depth", "d {}"), ("height", "h {}"),
                     ("length", "l {}"), ("min_size", "min {}")):
        if key in p and isinstance(p[key], (int, float)) and not isinstance(p[key], bool):
            bits.append(fmt.format(_fmt(p[key])))
            if len(bits) >= 4:
                break
    if p.get("from_history"):
        bits.insert(0, "from history")
    return " · ".join(bits)


def confidence_color(conf: float, threshold: float) -> str:
    if conf >= 0.8:
        return C_PASS
    if conf >= threshold:
        return C_WARNING
    return C_ERROR


def _item(kind: str, primary: str, secondary: str = "", color: str = C_MUTED) -> QStandardItem:
    it = QStandardItem()
    it.setEditable(False)
    it.setData(kind, R_TYPE)
    it.setData(primary, R_PRIMARY)
    it.setData(secondary, R_SECONDARY)
    it.setData(color, R_COLOR)
    it.setData(f"{primary} {secondary}".strip(), QtCore.Qt.ItemDataRole.DisplayRole)
    it.setData(f"{primary}  {secondary}".strip(), QtCore.Qt.ItemDataRole.AccessibleTextRole)
    return it


def feature_item(f, verdict: str, threshold: float) -> QStandardItem:
    # "Hole 4 (simple)": the subtype leads the summary, so it isn't repeated
    it = _item("feature", f.label.split(" (")[0], summary(f), kind_color(f.kind))
    it.setData(f.id, R_FID)
    it.setData(float(f.confidence), R_CONF)
    it.setData(confidence_color(f.confidence, threshold), QtCore.Qt.ItemDataRole.UserRole + 20)
    it.setData(verdict, R_VERDICT)
    it.setData(f.kind.value, R_KIND)
    it.setData(bool(f.params.get("from_history")), R_HISTORY)
    it.setToolTip(f"{f.label}\nFaces: {', '.join(f.face_labels)}\nConfidence {f.confidence:.2f}")
    return it


def group_item(label: str, color: str, features: list, verdicts: dict, kind: Optional[FeatureKind] = None):
    it = _item("group", label, "", color)
    it.setData(len(features), R_COUNT)
    it.setData(sum(verdicts.get(f.id) == "wrong" for f in features), R_WRONG)
    if kind is not None:
        it.setData(kind.value, R_KIND)
    return it


def build(model: QStandardItemModel, session, mode: str, threshold: float, show_unowned: bool = True,
          query: str = "") -> int:
    """Fill *model* from *session*. Returns the number of features shown."""
    model.clear()
    root = model.invisibleRootItem()
    r = session.result
    if r is None:
        return 0
    q = query.strip().lower()
    feats = [f for f in session.features()
             if not q or q in f.label.lower() or q in f.kind.value or q in (f.subtype or "").lower()
             or any(q == lab.lower() for lab in f.face_labels)]
    verdicts = session.verdicts

    if mode == GROUP_FLAT:
        for f in feats:
            root.appendRow(feature_item(f, verdicts.get(f.id, "unreviewed"), threshold))
    elif mode == GROUP_BY_REVIEW:
        names = (("wrong", "Marked wrong", C_ERROR), ("unreviewed", "To review", C_WARNING),
                 ("correct", "Marked correct", C_PASS))
        for key, label, color in names:
            members = [f for f in feats if verdicts.get(f.id, "unreviewed") == key]
            if not members:
                continue
            g = group_item(label, color, members, verdicts)
            for f in members:
                g.appendRow(feature_item(f, verdicts.get(f.id, "unreviewed"), threshold))
            root.appendRow(g)
    else:
        by_kind: dict = {}
        for f in feats:
            by_kind.setdefault(f.kind, []).append(f)
        for kind in sorted(by_kind, key=lambda k: _KIND_ORDER.get(k, 99)):
            members = by_kind[kind]
            label = _PLURAL.get(kind.value, kind.value.capitalize() + "s")
            g = group_item(label, kind_color(kind), members, verdicts, kind)
            for f in members:
                g.appendRow(feature_item(f, verdicts.get(f.id, "unreviewed"), threshold))
            root.appendRow(g)

    unowned = r.unowned_faces()
    if show_unowned and unowned and not q:
        g = _item("unowned", "No feature", "", UNOWNED_COLOR)
        g.setData(len(unowned), R_COUNT)
        g.setData(0, R_WRONG)
        for face in unowned:
            sig = r.face_signatures.get(face)
            text = f"{sig.kind} · {sig.area:.4g} mm²" if sig else ""
            fi = _item("face", f"Face{face}", text, UNOWNED_COLOR)
            fi.setData(face, R_FACE)
            g.appendRow(fi)
        root.appendRow(g)
    return len(feats)


class FeatureTreeDelegate(QtWidgets.QStyledItemDelegate):
    """Paints the bench's rows like the results tree's."""

    ROW_HEIGHT = 28
    BADGE_W = 38

    def __init__(self, parent=None):
        super().__init__(parent)
        self.actions_index = QtCore.QPersistentModelIndex()
        self.actions_width = 0

    def sizeHint(self, option, index):  # noqa: N802 (Qt naming)
        return QtCore.QSize(option.rect.width(), self.ROW_HEIGHT)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        kind = index.data(R_TYPE)
        primary = index.data(R_PRIMARY) or ""
        secondary = index.data(R_SECONDARY) or ""
        accent = QtGui.QColor(index.data(R_COLOR) or C_MUTED)

        selected = bool(option.state & QtWidgets.QStyle.StateFlag.State_Selected)
        if selected:
            painter.fillRect(option.rect, option.palette.highlight())
        text = option.palette.highlightedText().color() if selected else option.palette.text().color()
        muted = QtGui.QColor(text)
        muted.setAlphaF(0.6)

        rect = option.rect
        if self.actions_width and QtCore.QPersistentModelIndex(index) == self.actions_index:
            rect = rect.adjusted(0, 0, -self.actions_width, 0)
        mark = QtCore.QRect(rect.left() + 4, rect.top() + 4, 16, 16)
        text_left = mark.right() + 6

        if kind in ("group", "unowned"):
            sw = QtCore.QRectF(mark.center().x() - 5, mark.center().y() - 5, 10, 10)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawRoundedRect(sw, 2, 2)
            badges = []
            wrong = index.data(R_WRONG) or 0
            if wrong:
                badges.append((str(wrong), QtGui.QColor(C_ERROR)))
            badges.append((str(index.data(R_COUNT) or 0), QtGui.QColor("#777777")))
            x = rect.right() - 6
            for t, c in reversed(badges):
                x -= 28
                self._badge(painter, option, t, c, QtCore.QRect(x, rect.top() + 5, 28, rect.height() - 10))
                x -= 4
            font = QtGui.QFont(option.font)
            font.setWeight(QtGui.QFont.Weight.Medium)
            painter.setFont(font)
            painter.setPen(text)
            painter.drawText(QtCore.QRect(text_left, rect.top(), x - text_left - 6, rect.height()),
                             QtCore.Qt.AlignmentFlag.AlignVCenter, primary)
        elif kind == "feature":
            dot = QtCore.QRectF(mark.center().x() - 4.5, mark.center().y() - 4.5, 9, 9)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawEllipse(dot)
            # right end: verdict mark, confidence badge
            conf = index.data(R_CONF)
            right = rect.right() - 6
            if conf is not None:
                c = QtGui.QColor(index.data(QtCore.Qt.ItemDataRole.UserRole + 20) or C_MUTED)
                right -= self.BADGE_W
                self._badge(painter, option, f"{conf:.2f}", c,
                            QtCore.QRect(right, rect.top() + 5, self.BADGE_W, rect.height() - 10))
                right -= 4
            verdict = index.data(R_VERDICT)
            if verdict in ("correct", "wrong"):
                right -= 16
                self._verdict_mark(painter, QtCore.QRectF(right, rect.top() + 6, 16, 16), verdict)
                right -= 4
            font = QtGui.QFont(option.font)
            painter.setFont(font)
            fm = QtGui.QFontMetrics(font)
            name_w = min(fm.horizontalAdvance(primary) + 8, max(right - text_left - 20, 0))
            painter.setPen(text)
            painter.drawText(QtCore.QRect(text_left, rect.top(), name_w, rect.height()),
                             QtCore.Qt.AlignmentFlag.AlignVCenter,
                             fm.elidedText(primary, QtCore.Qt.TextElideMode.ElideRight, name_w))
            if secondary:
                sfont = QtGui.QFont(font)
                if index.data(R_HISTORY):
                    sfont.setItalic(True)
                painter.setFont(sfont)
                painter.setPen(muted)
                x = text_left + name_w + 2
                avail = max(right - x - 6, 0)
                shown = QtGui.QFontMetrics(sfont).elidedText("·  " + secondary,
                                                             QtCore.Qt.TextElideMode.ElideRight, avail)
                painter.drawText(QtCore.QRect(x, rect.top(), avail, rect.height()),
                                 QtCore.Qt.AlignmentFlag.AlignVCenter, shown)
        else:  # face
            painter.setPen(muted)
            painter.setFont(option.font)
            fm = QtGui.QFontMetrics(option.font)
            line = primary + ("  ·  " + secondary if secondary else "")
            painter.drawText(QtCore.QRect(text_left, rect.top(), rect.right() - text_left - 6, rect.height()),
                             QtCore.Qt.AlignmentFlag.AlignVCenter,
                             fm.elidedText(line, QtCore.Qt.TextElideMode.ElideRight, rect.right() - text_left - 6))
        painter.restore()

    @staticmethod
    def _badge(painter, option, text, color, rect):
        bg = QtGui.QColor(color)
        bg.setAlpha(190)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 3, 3)
        font = QtGui.QFont(option.font)
        font.setPointSizeF(option.font.pointSizeF() * 0.85)
        painter.setFont(font)
        painter.setPen(QtGui.QColor("white"))
        painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, text)

    @staticmethod
    def _verdict_mark(painter, rect: QtCore.QRectF, verdict: str):
        color = QtGui.QColor(C_PASS if verdict == "correct" else C_ERROR)
        pen = QtGui.QPen(color, 2.2)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        if verdict == "correct":
            path = QtGui.QPainterPath(QtCore.QPointF(x + 0.18 * w, y + 0.55 * h))
            path.lineTo(x + 0.42 * w, y + 0.78 * h)
            path.lineTo(x + 0.84 * w, y + 0.25 * h)
            painter.drawPath(path)
        else:
            painter.drawLine(QtCore.QPointF(x + 0.25 * w, y + 0.25 * h), QtCore.QPointF(x + 0.75 * w, y + 0.75 * h))
            painter.drawLine(QtCore.QPointF(x + 0.75 * w, y + 0.25 * h), QtCore.QPointF(x + 0.25 * w, y + 0.75 * h))
