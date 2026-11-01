# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from PySide6 import QtCore, QtGui, QtWidgets

from ..rule_cards import INFO_SVG, render_svg_icon

# Rule rows carry their Rulebook member in this role; it enables the info icon.
RULE_ROLE = QtCore.Qt.ItemDataRole.UserRole + 9

_INFO_SIZE = 14
_INFO_GAP = 6


class DFMTreeDelegate(QtWidgets.QStyledItemDelegate):
    """Custom delegate that renders rule rows and finding rows with distinct visual styles.

    Rule rows that carry a rule (``RULE_ROLE``) get a small info icon after
    the name; hovering it shows the rule's diagram. ``info_rect`` returns
    where that icon is drawn, so the view can detect the hover.
    """

    _LABEL_COLOR = QtGui.QColor("#aaaaaa")
    _IGNORE_COLOR = QtGui.QColor("#666666")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._info_icons: dict[tuple, QtGui.QIcon] = {}
        self.hovered_info: QtCore.QPersistentModelIndex = QtCore.QPersistentModelIndex()
        # The row showing the camera buttons, and the room they need at its
        # right end; set by the view.
        self.actions_index: QtCore.QPersistentModelIndex = QtCore.QPersistentModelIndex()
        self.actions_width: int = 0

    @staticmethod
    def _label_font(option_font: QtGui.QFont) -> QtGui.QFont:
        font = QtGui.QFont(option_font)
        font.setWeight(QtGui.QFont.Weight.Medium)
        return font

    def info_rect(self, view: QtWidgets.QAbstractItemView, index: QtCore.QModelIndex) -> QtCore.QRect:
        """Viewport rectangle of the info icon on *index*, or an empty rect."""
        if not index.isValid() or index.data(RULE_ROLE) is None:
            return QtCore.QRect()
        return self._info_rect_for(view.visualRect(index), index, view.font())

    def _info_rect_for(self, rect: QtCore.QRect, index, font: QtGui.QFont) -> QtCore.QRect:
        text_left = rect.left() + 4 + 16 + 6
        label = index.data(QtCore.Qt.ItemDataRole.UserRole + 2) or ""
        width = QtGui.QFontMetrics(self._label_font(font)).horizontalAdvance(label)
        x = text_left + width + _INFO_GAP
        y = rect.top() + (rect.height() - _INFO_SIZE) // 2
        return QtCore.QRect(x, y, _INFO_SIZE, _INFO_SIZE)

    def _info_icon(self, color: QtGui.QColor, ratio: float) -> QtGui.QIcon:
        key = (color.rgba(), ratio)
        if key not in self._info_icons:
            self._info_icons[key] = render_svg_icon(INFO_SVG, color, _INFO_SIZE, ratio)
        return self._info_icons[key]

    def paint(self, painter, option, index):
        painter.save()

        item_type = index.data(QtCore.Qt.ItemDataRole.UserRole + 1)
        primary = index.data(QtCore.Qt.ItemDataRole.UserRole + 2) or ""
        secondary = index.data(QtCore.Qt.ItemDataRole.UserRole + 3) or ""
        color_hex = index.data(QtCore.Qt.ItemDataRole.UserRole + 4) or "#aaaaaa"
        ignored = index.data(QtCore.Qt.ItemDataRole.UserRole + 5) or False

        is_selected = option.state & QtWidgets.QStyle.StateFlag.State_Selected
        if is_selected:
            painter.fillRect(option.rect, option.palette.highlight())

        icon = index.data(QtCore.Qt.ItemDataRole.DecorationRole)
        icon_rect = QtCore.QRect(option.rect.left() + 4, option.rect.top() + 4, 16, 16)

        text_left = icon_rect.right() + 6
        if icon and not icon.isNull():
            icon.paint(painter, icon_rect)

        text_left = icon_rect.right() + 6
        rect = option.rect
        if self.actions_width and QtCore.QPersistentModelIndex(index) == self.actions_index:
            rect = rect.adjusted(0, 0, -self.actions_width, 0)  # room for the camera buttons
        accent = QtGui.QColor(color_hex)

        base_text = (
            option.palette.highlightedText().color()
            if is_selected
            else option.palette.text().color()
        )

        muted_text = QtGui.QColor(base_text)
        muted_text.setAlphaF(0.45)

        ignored_color = QtGui.QColor(base_text)
        ignored_color.setAlphaF(0.35)

        if item_type in ("rule", "all", "criticality"):
            error_count = index.data(QtCore.Qt.ItemDataRole.UserRole + 6) or 0
            warning_count = index.data(QtCore.Qt.ItemDataRole.UserRole + 7) or 0

            badge_font = QtGui.QFont(option.font)
            badge_font.setPointSizeF(option.font.pointSizeF() * 0.85)

            ignored_count = index.data(QtCore.Qt.ItemDataRole.UserRole + 8) or 0

            # Fixed-width badges packed right-to-left (no empty gaps).
            # Ordered: error (rightmost), warning, ignored (leftmost).
            BADGE_W = 28
            badge_spacing = 4

            badges = []
            if ignored_count:
                badges.append((str(ignored_count), QtGui.QColor("#666666")))
            if warning_count:
                badges.append((str(warning_count), QtGui.QColor("#D4900A")))
            if error_count:
                badges.append((str(error_count), QtGui.QColor("#E24B4A")))

            n = len(badges)
            total_badge_w = n * BADGE_W + max(0, n - 1) * badge_spacing if n else 0

            label_rect = QtCore.QRect(
                text_left, rect.top(), rect.right() - total_badge_w - 14 - text_left, rect.height()
            )
            label_font = QtGui.QFont(option.font)
            label_font.setWeight(QtGui.QFont.Weight.Medium)
            painter.setFont(label_font)
            painter.setPen(base_text)
            painter.drawText(label_rect, QtCore.Qt.AlignmentFlag.AlignVCenter, primary)

            # Info icon after the rule name: faded at rest, full on hover.
            if index.data(RULE_ROLE) is not None:
                info = self._info_rect_for(rect, index, option.font)
                if info.right() < rect.right() - total_badge_w - 8:
                    color = QtGui.QColor(base_text)
                    hovered = QtCore.QPersistentModelIndex(index) == self.hovered_info
                    mode = QtGui.QIcon.Mode.Active if hovered else QtGui.QIcon.Mode.Normal
                    painter.setOpacity(1.0 if hovered else 0.6)
                    ratio = painter.device().devicePixelRatioF() if painter.device() else 1.0
                    self._info_icon(color, ratio).paint(
                        painter, info, QtCore.Qt.AlignmentFlag.AlignCenter, mode
                    )
                    painter.setOpacity(1.0)

            def draw_badge(text, color, x):
                badge_rect = QtCore.QRect(x, rect.top() + 5, BADGE_W, rect.height() - 10)
                bg = QtGui.QColor(color)
                bg.setAlpha(180)
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.setBrush(bg)
                painter.drawRoundedRect(badge_rect, 3, 3)
                painter.setFont(badge_font)
                painter.setPen(QtGui.QColor("white"))
                painter.drawText(badge_rect, QtCore.Qt.AlignmentFlag.AlignCenter, text)

            # Draw right-to-left: last in list → rightmost on screen.
            x = rect.right() - 6
            for text, color in reversed(badges):
                x -= BADGE_W
                draw_badge(text, color, x)
                x -= badge_spacing

        elif item_type == "finding":
            name_font = QtGui.QFont(option.font)
            if ignored:
                name_font.setStrikeOut(True)

            if is_selected:
                primary_color = option.palette.highlightedText().color()
            else:
                primary_color = option.palette.text().color()

            secondary_color = QtGui.QColor(primary_color)
            secondary_color.setAlphaF(0.35 if ignored else 0.6)

            painter.setFont(name_font)
            painter.setPen(secondary_color if ignored else primary_color)

            fm = QtGui.QFontMetrics(name_font)
            name_w = fm.horizontalAdvance(primary) + 8
            name_rect = QtCore.QRect(text_left, rect.top(), name_w, rect.height())
            painter.drawText(name_rect, QtCore.Qt.AlignmentFlag.AlignVCenter, primary)

            dot_x = text_left + name_w + 2
            dot_rect = QtCore.QRect(dot_x, rect.top(), 12, rect.height())
            painter.setPen(secondary_color)
            painter.drawText(dot_rect, QtCore.Qt.AlignmentFlag.AlignVCenter, "·")

            painter.setFont(name_font)
            painter.setPen(secondary_color if ignored else primary_color)
            overview_rect = QtCore.QRect(
                dot_x + 14, rect.top(), rect.right() - dot_x - 14, rect.height()
            )
            shown = fm.elidedText(secondary, QtCore.Qt.TextElideMode.ElideRight,
                                  max(overview_rect.width(), 0))
            painter.drawText(overview_rect, QtCore.Qt.AlignmentFlag.AlignVCenter, shown)

        painter.restore()

    def sizeHint(self, option, index):
        return QtCore.QSize(option.rect.width(), 28)


class HistoryRowDelegate(QtWidgets.QStyledItemDelegate):
    """
    Renders history rows with fixed-width badges for vertical alignment.
    Layout: [Icon] Rule Label (flexible) [Badge Fixed] → [Badge Fixed]
    """

    BADGE_WIDTH = 45
    ARROW_WIDTH = 20
    RIGHT_MARGIN = 8
    SPACING = 4

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        #  Extract data
        rule_label = index.data(QtCore.Qt.ItemDataRole.UserRole + 2) or ""
        prev_text = index.data(QtCore.Qt.ItemDataRole.UserRole + 3) or ""
        prev_color = index.data(QtCore.Qt.ItemDataRole.UserRole + 4) or "#666666"
        curr_text = index.data(QtCore.Qt.ItemDataRole.UserRole + 5) or ""
        curr_color = index.data(QtCore.Qt.ItemDataRole.UserRole + 6) or "#666666"

        is_selected = option.state & QtWidgets.QStyle.StateFlag.State_Selected
        rect = option.rect

        # Draw background
        if is_selected:
            painter.fillRect(rect, option.palette.highlight())

        base_text_color = (
            option.palette.highlightedText().color()
            if is_selected
            else option.palette.text().color()
        )

        muted_text = QtGui.QColor(base_text_color)
        muted_text.setAlphaF(0.4)

        # Draw icon
        icon = index.data(QtCore.Qt.ItemDataRole.DecorationRole)
        icon_rect = QtCore.QRect(rect.left() + 6, rect.top() + (rect.height() - 16) // 2, 16, 16)
        if icon and not icon.isNull():
            icon.paint(painter, icon_rect)

        # Define UI positions (Right to Left)
        curr_x = rect.right() - self.RIGHT_MARGIN - self.BADGE_WIDTH
        arrow_x = curr_x - self.ARROW_WIDTH
        prev_x = arrow_x - self.BADGE_WIDTH

        # Helper to draw Badge
        def draw_badge(text, color_hex, x):
            # Badge background
            badge_rect = QtCore.QRect(x, rect.top() + 5, self.BADGE_WIDTH, rect.height() - 10)
            bg = QtGui.QColor(color_hex)
            bg.setAlpha(180)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(badge_rect, 3, 3)

            # Badge text
            badge_font = QtGui.QFont(option.font)
            badge_font.setPointSizeF(option.font.pointSizeF() * 0.8)
            painter.setFont(badge_font)
            painter.setPen(QtGui.QColor("white"))
            painter.drawText(badge_rect, QtCore.Qt.AlignmentFlag.AlignCenter, text)

        # Draw badges and arrow
        draw_badge(curr_text, curr_color, curr_x)

        painter.setFont(option.font)
        painter.setPen(muted_text)
        arrow_rect = QtCore.QRect(arrow_x, rect.top(), self.ARROW_WIDTH, rect.height())
        painter.drawText(arrow_rect, QtCore.Qt.AlignmentFlag.AlignCenter, "→")

        draw_badge(prev_text, prev_color, prev_x)

        # Draw rule label
        text_left = icon_rect.right() + 8
        label_w = prev_x - text_left - self.SPACING
        label_rect = QtCore.QRect(text_left, rect.top(), label_w, rect.height())

        label_font = QtGui.QFont(option.font)
        label_font.setWeight(QtGui.QFont.Weight.Medium)
        painter.setFont(label_font)
        painter.setPen(base_text_color)

        elided_text = QtGui.QFontMetrics(label_font).elidedText(
            rule_label, QtCore.Qt.TextElideMode.ElideRight, label_w
        )
        painter.drawText(label_rect, QtCore.Qt.AlignmentFlag.AlignVCenter, elided_text)

        painter.restore()

    def sizeHint(self, option, index):
        return QtCore.QSize(option.rect.width(), 28)
