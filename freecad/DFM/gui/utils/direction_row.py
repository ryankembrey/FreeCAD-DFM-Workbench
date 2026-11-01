# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The direction row shared by the setup and contour panels.

::

    Pull direction        Face12  [+X|+Y|-Z]  [pick] [flip]

The label, the face or edge the direction came from (only when it came
from one), three axis buttons, a button to pick a face or edge in the 3D
view and a flip button. The row only shows the direction; the panel that
owns it keeps the value and reacts to the signals.

Also home to the small pieces both panels style their buttons with.
"""

from typing import Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from .widgets import make_flip_button

# A mouse pointer: "pick in the 3D view".
PICK_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="COLOR"'
    b' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    b'<path d="M5 3l14 8-6.5 1.6L10 19z"/></svg>'
)

# A filled triangle for buttons that start something.
PLAY_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    b'<path d="M7 4l13 8-13 8z" fill="COLOR"/></svg>'
)

AXES = (("X", (1.0, 0.0, 0.0)), ("Y", (0.0, 1.0, 0.0)), ("Z", (0.0, 0.0, 1.0)))

AXIS_CSS = """
QToolButton { border: 1px solid rgba(127, 127, 127, 110); border-radius: 0px;
              padding: 2px 5px; background: transparent; min-width: 22px; }
QToolButton:hover { background: rgba(127, 127, 127, 40); }
QToolButton:checked { background: rgba(127, 127, 127, 85); border-color: rgba(127, 127, 127, 170);
                      font-weight: bold; }
QToolButton#first { border-top-left-radius: 3px; border-bottom-left-radius: 3px; }
QToolButton#last { border-top-right-radius: 3px; border-bottom-right-radius: 3px; }
QToolButton#mid, QToolButton#last { border-left: none; }
"""

# Push buttons, not tool buttons: some FreeCAD themes give tool buttons a
# small fixed icon size, which shrank the pick icon to a dot. The icon size
# is also set here, as a widget stylesheet outranks the theme's.
ICON_BUTTON_CSS = """
QPushButton { border: 1px solid rgba(127, 127, 127, 110); border-radius: 3px; background: transparent;
              padding: 0px; min-width: 0px; qproperty-iconSize: 16px 16px; }
QPushButton:hover { background: rgba(127, 127, 127, 40); }
QPushButton:checked { background: rgba(127, 127, 127, 85); border-color: rgba(127, 127, 127, 170); }
"""


def muted_color(widget: QtWidgets.QWidget, alpha: float = 0.65) -> str:
    """The theme's text color, faded, as a CSS color for rich text."""
    probe = QtWidgets.QLabel(widget)
    probe.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen)
    probe.hide()
    probe.ensurePolished()
    c = probe.palette().color(QtGui.QPalette.ColorRole.WindowText)
    probe.setParent(None)
    probe.deleteLater()
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


def icon_button(svg: bytes, tooltip: str, checkable: bool = False) -> QtWidgets.QPushButton:
    """A small square button with a monochrome icon in the theme's text color."""
    from ..rule_cards import apply_svg_icon  # here, to avoid an import cycle

    btn = QtWidgets.QPushButton()
    btn.setCheckable(checkable)
    btn.setToolTip(tooltip)
    btn.setFixedSize(28, 26)
    btn.setStyleSheet(ICON_BUTTON_CSS)
    btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
    apply_svg_icon(btn, svg, 16)
    return btn


def style_primary_button(button: QtWidgets.QPushButton, text: str) -> None:
    """The theme's own button look, bold, with a play icon: "this starts it"."""
    from ..rule_cards import apply_svg_icon

    button.setText(text)
    font = button.font()
    font.setBold(True)
    button.setFont(font)
    button.setMinimumHeight(30)
    apply_svg_icon(button, PLAY_SVG, 14)


def axis_of(direction: Optional[Sequence[float]]) -> tuple[int, int]:
    """(axis, sign) when *direction* lies along X, Y or Z; else (-1, 0)."""
    if direction is None:
        return -1, 0
    v = tuple(float(c) for c in direction)
    length = sum(c * c for c in v) ** 0.5
    if length == 0:
        return -1, 0
    for i, (_n, axis) in enumerate(AXES):
        dot = sum(a * b for a, b in zip(v, axis)) / length
        if abs(abs(dot) - 1.0) < 1e-6:
            return i, (1 if dot > 0 else -1)
    return -1, 0


def is_element_ref(ref: str) -> bool:
    """True when *ref* names a face or edge, not an axis such as "+Z"."""
    return bool(ref) and ref[0] not in "+-−"


class DirectionRow(QtWidgets.QWidget):
    """Label, picked face or edge, +X/+Y/+Z, pick and flip, on one line."""

    axis_clicked = QtCore.Signal(tuple)
    pick_clicked = QtCore.Signal()
    flip_clicked = QtCore.Signal()

    def __init__(self, label: str, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self.label = QtWidgets.QLabel(label)
        self.label.setMinimumHeight(28)
        row.addWidget(self.label)
        row.addStretch(1)

        self.ref_label = QtWidgets.QLabel()
        self.ref_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        row.addWidget(self.ref_label)
        row.addSpacing(2)

        axes = QtWidgets.QHBoxLayout()
        axes.setSpacing(0)
        self.axis_btns: list[QtWidgets.QToolButton] = []
        for i, (name, vec) in enumerate(AXES):
            btn = QtWidgets.QToolButton()
            btn.setText(f"+{name}")
            btn.setCheckable(True)
            btn.setObjectName("first" if i == 0 else ("last" if i == len(AXES) - 1 else "mid"))
            btn.setStyleSheet(AXIS_CSS)
            btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            btn.setToolTip(f"Point along the {name} axis. Click again to reverse it.")
            # Keep the check state in step with the value, not with clicks.
            btn.clicked.connect(lambda _c=False, v=vec: self._on_axis(v))
            axes.addWidget(btn)
            self.axis_btns.append(btn)
        row.addLayout(axes)
        row.addSpacing(4)

        self.pick_btn = icon_button(PICK_SVG, f"Pick a face or edge for the {label.lower()}",
                                    checkable=True)
        self.pick_btn.clicked.connect(lambda _c=False: self.pick_clicked.emit())
        row.addWidget(self.pick_btn)
        self.flip_btn = make_flip_button(lambda _c=False: self.flip_clicked.emit(), size=(28, 26))
        row.addWidget(self.flip_btn)

        self._state = (None, "", False, False, "")

    def _on_axis(self, vec: tuple) -> None:
        self.axis_clicked.emit(vec)
        self.set_state(*self._state)  # a click toggles the check; put it back

    def set_state(
        self,
        direction: Optional[Sequence[float]],
        ref: str = "",
        flipped: bool = False,
        picking: bool = False,
        hint: str = "Click a face or edge",
    ) -> None:
        """Show *direction*, which came from *ref* (an axis like "+Z" or "Face12")."""
        self._state = (direction, ref, flipped, picking, hint)
        index, sign = axis_of(direction)
        for i, btn in enumerate(self.axis_btns):
            btn.setChecked(i == index)
            prefix = "−" if (i == index and sign < 0) else "+"
            btn.setText(prefix + AXES[i][0])
        self.pick_btn.setChecked(picking)
        element = is_element_ref(ref)
        if picking:
            text = hint
        elif element:
            text = f"{ref}{' (flipped)' if flipped else ''}"
        else:
            text = ""
        self.ref_label.setText(
            f'<span style="color: {muted_color(self.ref_label)}">{text}</span>' if text else "")
        self.ref_label.setToolTip(
            f"Uses the direction of {ref}{', reversed' if flipped else ''}"
            if element and not picking else "")
        self.ref_label.setVisible(bool(text))
        self.flip_btn.setEnabled(direction is not None)


class ViewSwitch(QtWidgets.QWidget):
    """Two joined buttons, one of them pressed, such as Color scale | Pass/fail.

    Reads like a check box so callers can treat it as one: ``isChecked()``
    is True when the second option is pressed, and ``toggled`` fires when
    the choice changes.
    """

    toggled = QtCore.Signal(bool)

    def __init__(self, first: str, second: str, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: list[QtWidgets.QToolButton] = []
        for i, text in enumerate((first, second)):
            btn = QtWidgets.QToolButton()
            btn.setText(text)
            btn.setCheckable(True)
            btn.setObjectName("first" if i == 0 else "last")
            btn.setStyleSheet(AXIS_CSS)
            btn.setFocusPolicy(QtCore.Qt.FocusPolicy.TabFocus)
            btn.setMinimumHeight(26)
            btn.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                              QtWidgets.QSizePolicy.Policy.Fixed)
            self._group.addButton(btn, i)
            row.addWidget(btn, 1)
            self.buttons.append(btn)
        self.buttons[0].setChecked(True)
        self._group.idToggled.connect(self._on_toggled)

    def _on_toggled(self, button_id: int, checked: bool) -> None:
        if checked:
            self.toggled.emit(button_id == 1)

    def isChecked(self) -> bool:  # noqa: N802 (Qt naming)
        return self.buttons[1].isChecked()

    def setChecked(self, second: bool) -> None:  # noqa: N802 (Qt naming)
        """Press the second option when *second* is True, else the first."""
        # Button signals are separate from ours: keep blockSignals() on this
        # widget working by emitting from here only.
        self._group.blockSignals(True)
        changed = self.isChecked() != bool(second)
        self.buttons[1 if second else 0].setChecked(True)
        self._group.blockSignals(False)
        if changed:
            self.toggled.emit(bool(second))
