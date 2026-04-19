# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the DFM addon.

#  ***************************************************************************
#  *   Copyright (c) 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>              *
#  *                                                                         *
#  *   This file is part of the FreeCAD CAx development system.              *
#  *                                                                         *
#  *   This library is free software; you can redistribute it and/or         *
#  *   modify it under the terms of the GNU Library General Public           *
#  *   License as published by the Free Software Foundation; either          *
#  *   version 2 of the License, or (at your option) any later version.      *
#  *                                                                         *
#  *   This library  is distributed in the hope that it will be useful,      *
#  *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
#  *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
#  *   GNU Library General Public License for more details.                  *
#  *                                                                         *
#  *   You should have received a copy of the GNU Library General Public     *
#  *   License along with this library; see the file COPYING.LIB. If not,   *
#  *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
#  *   Suite 330, Boston, MA  02111-1307, USA                                *
#  *                                                                         *
#  ***************************************************************************

from PySide6 import QtCore, QtGui, QtWidgets


class DFMTreeDelegate(QtWidgets.QStyledItemDelegate):
    """Custom delegate that renders rule rows and finding rows with distinct visual styles."""

    _LABEL_COLOR = QtGui.QColor("#aaaaaa")
    _IGNORE_COLOR = QtGui.QColor("#666666")

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
        if icon and not icon.isNull():
            icon.paint(painter, icon_rect)

        text_left = icon_rect.right() + 6
        rect = option.rect
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

        if item_type == "rule":
            error_count = index.data(QtCore.Qt.ItemDataRole.UserRole + 6) or 0
            warning_count = index.data(QtCore.Qt.ItemDataRole.UserRole + 7) or 0

            badge_font = QtGui.QFont(option.font)
            badge_font.setPointSizeF(option.font.pointSizeF() * 0.85)
            fm_badge = QtGui.QFontMetrics(badge_font)

            badges = []
            if not error_count and not warning_count:
                badges.append((secondary, QtGui.QColor("#639922")))
            else:
                if warning_count:
                    badges.append((str(warning_count), QtGui.QColor("#D4900A")))
                if error_count:
                    badges.append((str(error_count), QtGui.QColor("#E24B4A")))

            badge_spacing = 4
            badge_widths = [fm_badge.horizontalAdvance(f"  {t}  ") + 8 for t, _ in badges]
            total_badge_w = sum(badge_widths) + badge_spacing * (len(badges) - 1)

            label_rect = QtCore.QRect(
                text_left, rect.top(), rect.right() - total_badge_w - 14 - text_left, rect.height()
            )
            label_font = QtGui.QFont(option.font)
            label_font.setWeight(QtGui.QFont.Weight.Medium)
            painter.setFont(label_font)
            painter.setPen(base_text)
            painter.drawText(label_rect, QtCore.Qt.AlignmentFlag.AlignVCenter, primary)

            def draw_badge(text, color, x, w):
                badge_rect = QtCore.QRect(x, rect.top() + 5, w, rect.height() - 10)
                bg = QtGui.QColor(color)
                bg.setAlpha(180)
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.setBrush(bg)
                painter.drawRoundedRect(badge_rect, 3, 3)
                painter.setFont(badge_font)
                painter.setPen(QtGui.QColor("white"))
                painter.drawText(badge_rect, QtCore.Qt.AlignmentFlag.AlignCenter, text)

            x = rect.right() - 6
            for (text, color), w in zip(reversed(badges), reversed(badge_widths)):
                x -= w
                draw_badge(text, color, x, w)
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
            painter.drawText(overview_rect, QtCore.Qt.AlignmentFlag.AlignVCenter, secondary)

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
