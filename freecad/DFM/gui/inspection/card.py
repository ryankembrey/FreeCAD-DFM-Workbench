# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The card in the 3D view for the selected difference.

It is the results panel's finding card: the blue info icon, the
difference's name in bold and a short subtitle in the header, what it is
and what to do in the body (the arrow folds it), dragged and kept on a
leader line by :class:`FindingOverlay`, with Ignore / Restore on
right-click.
"""

from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets

import FreeCAD as App  # type: ignore

from ...core.inspection import geometry as G
from ...core.inspection import models as M
from ...core.inspection.naming import fmt_volume
from ..results.overlay import FindingCard, FindingOverlay, _ResizeFilter
from ..utils.view import get_active_view, get_view_widget
from .widgets import INFO_HEX, info_icon

try:
    from pivy import coin
except Exception:  # pragma: no cover
    coin = None

_CARD_WIDTH = 320


def _coords(p) -> str:
    return ", ".join(f"{axis} {v:.1f}".replace("-", "−") for axis, v in zip("XYZ", p))


def _thing(name: str) -> str:
    return name[:1].lower() + name[1:] if name else "it"


def explanation(d: M.Difference, rotational: bool = False) -> str:
    """What the card says about *d*: what it is, and what to do."""
    vol = fmt_volume(abs(d.volume))
    where = ""
    if d.ghosts and d.kind in (M.EXTRA, M.CUT):
        try:
            where = _coords(G.bounding_box(G.compound(d.ghosts)).centre)
        except Exception:
            where = ""
    partner = "copy" if rotational else "mirror image"
    if d.kind == M.EXTRA:
        link = "has" if d.detail.startswith("no ") else "is"
        text = f"This {_thing(d.name)} {link} {d.detail} ({vol}). "
        if where:
            text += f"Its {partner} would be at {where}. "
        return text + "Add a matching one there, or ignore it if the part is meant to differ."
    if d.kind == M.CUT:
        link = "has" if d.detail.startswith("no ") else "is"
        text = f"This {_thing(d.name)} {link} {d.detail} ({vol} of material). "
        if where:
            text += f"Where its {partner} would be, at {where}, the part is solid. "
        return text + "Cut a matching one there, or ignore it if the part is meant to differ."
    if d.kind == M.MISMATCH:
        return (f"This {_thing(d.name)} and its {'copy' if rotational else 'partner'} don’t line up: "
                f"{d.detail}. Line them up, or ignore it if the part is meant to differ.")
    if d.kind == M.ADDED:
        return f"{d.detail}, {fmt_volume(d.volume, signed=True)}. A has no material here."
    if d.kind == M.REMOVED:
        return f"{d.detail}, {fmt_volume(d.volume, signed=True)}. B has no material here."
    return f"{d.name}: {d.detail} ({fmt_volume(d.volume, signed=True)} in all)."


def subtitle(d: M.Difference) -> str:
    """The card header's small text: where it is, or what happened to it."""
    if d.ignored:
        return "ignored"
    if d.kind in (M.EXTRA, M.CUT, M.MISMATCH):
        return d.detail
    return {M.ADDED: "added in B", M.REMOVED: "removed from A", M.CHANGED: "changed"}.get(d.kind, "")


class DifferenceCard(FindingCard):
    """A :class:`FindingCard` for a difference rather than a finding.

    ``ignore_requested(difference)`` from the right-click menu.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        self._difference: Optional[M.Difference] = None
        super().__init__(parent)
        # No rule, so no rule diagram.
        self._diagram_btn.hide()
        self._diagram_visible = False

    @property
    def difference(self) -> Optional[M.Difference]:
        return self._difference

    def set_difference(self, difference: M.Difference, rotational: bool = False) -> None:
        self._difference = difference
        ratio = self.devicePixelRatioF() or 1.0
        self._icon_label.setPixmap(info_icon(difference.ignored).pixmap(QtCore.QSize(18, 18), ratio))
        self._value_label.setText(difference.name)
        self._rule_label.setText(subtitle(difference))
        self._feedback_label.setText(explanation(difference, rotational))
        self._toggle_btn.setVisible(True)
        # Open: the explanation is what the card is for.
        self._feedback_visible = True
        self._separator.setVisible(True)
        self._feedback_frame.setVisible(True)
        self._toggle_btn.setArrowType(QtCore.Qt.ArrowType.UpArrow)
        self._update_header_radius()
        self._apply_theme()
        self.setFixedWidth(max(_CARD_WIDTH, self._header.sizeHint().width() + 2))
        self.adjustSize()

    def _accent_hex(self) -> Optional[str]:
        return INFO_HEX if self._difference is not None else None

    def contextMenuEvent(self, event):  # noqa: N802
        if self._difference is None:
            return super().contextMenuEvent(event)
        menu = QtWidgets.QMenu(self)
        action = menu.addAction("Restore" if self._difference.ignored else "Ignore")
        if menu.exec(event.globalPos()) is action:
            self.ignore_requested.emit(self._difference)


class DifferenceOverlay(FindingOverlay):
    """A :class:`DifferenceCard` on a leader line to its difference."""

    def __init__(self):
        super().__init__()
        self.ignore_requested = None  # callback(difference)

    def show_difference(self, difference: M.Difference, rotational: bool = False) -> None:
        self.remove()
        anchor = difference.anchor
        self._view = get_active_view()
        self._viewport_widget = get_view_widget()
        if self._view is None or self._viewport_widget is None or anchor is None:
            self._view = None
            self._viewport_widget = None
            return
        self._anchor = App.Vector(*anchor)
        self._drag_offset = None
        card = DifferenceCard(self._viewport_widget)
        card.set_difference(difference, rotational)
        card.dragged.connect(self._on_card_dragged)
        card.ignore_requested.connect(lambda d: self.ignore_requested and self.ignore_requested(d))
        self._card = card
        self._build_leader_line(INFO_HEX)
        try:
            camera = self._view.getCameraNode()
            if camera and coin is not None:
                self._cam_sensor = coin.SoNodeSensor(self._on_camera_changed, None)
                self._active_camera = camera
                self._cam_sensor.attach(camera)
        except Exception:
            pass
        self._viewport_widget.installEventFilter(_ResizeFilter.instance(self))
        self._reposition()
        card.show()

    @property
    def difference(self) -> Optional[M.Difference]:
        return self._card.difference if isinstance(self._card, DifferenceCard) else None
