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
#  *   License along with this library; see the file COPYING.LIB. If not,    *
#  *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
#  *   Suite 330, Boston, MA  02111-1307, USA                                *
#  *                                                                         *
#  ***************************************************************************

import csv
import html
from collections import defaultdict
from typing import Any, Callable
import base64

import FreeCAD  # type: ignore
import FreeCADGui as Gui  # type: ignore
import Part  # type: ignore
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtGui import QStandardItemModel, QStandardItem
from PySide6.QtWidgets import QFileDialog, QMessageBox

from dfm.models import CheckResult, Severity
from dfm.processes.process import Process
from dfm.rules import Rulebook


def _severity_color(severity: Severity) -> str:
    return {
        Severity.ERROR: "#E24B4A",
        Severity.WARNING: "#D4900A",
        Severity.INFO: "#378ADD",
        Severity.SUCCESS: "#639922",
    }.get(severity, "#639922")


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


class TaskResults:
    """Passive View: Only handles Widgets and Signals."""

    def __init__(self):
        self.form: Any = Gui.PySideUic.loadUi(":/ui/task_results.ui", None)  # type: ignore
        self.form.setWindowTitle("DFM Analysis")
        icon = QtGui.QIcon(":/icons/dfm_analysis.svg")
        self.form.setWindowIcon(icon)
        self.model = QStandardItemModel()
        self.form.tvResults.setModel(self.model)
        self.form.tvResults.setHeaderHidden(True)

        self.form.leTarget.setReadOnly(True)
        self.form.leProcess.setReadOnly(True)
        self.form.leMaterial.setReadOnly(True)
        self.form.leVerdict.setReadOnly(True)
        self.form.tbDetails.setReadOnly(True)

        self.form.tbDetails.setHtml(
            "Select a result in the tree to view details of the DFM issues."
        )

        self._save_clicked = False

        self.on_closed: Callable[[], None] | None = None
        self.on_row_selected: Callable[[CheckResult | list[CheckResult]], None] | None = None
        self.on_row_double_clicked: Callable[[CheckResult], None] | None = None
        self.on_toggle_ignore: Callable[[CheckResult], None] | None = None
        self.on_export_clicked: Callable[[], None] | None = None
        self.on_toggle_ignore_all: Callable[[list[CheckResult]], None] | None = None
        self.on_zoom_to_rule: Callable[[list[CheckResult]], None] | None = None

        self.form.tvResults.clicked.connect(self._handle_click)
        self.form.tvResults.doubleClicked.connect(self._handle_double_click)
        self.form.pbExportResults.clicked.connect(self._handle_export_btn)
        self.form.tvResults.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.form.tvResults.customContextMenuRequested.connect(self._show_context_menu)

    def adjust_details_height(self):
        """Dynamic resizing of the description box based on content."""
        doc = self.form.tbDetails.document()
        content_height = doc.documentLayout().documentSize().height()
        final_height = int(content_height) + 10
        self.form.tbDetails.setFixedHeight(max(60, min(final_height, 300)))

    def render_tree(self, grouped_data: dict, face_namer_func: Callable, all_process_rules: list):
        """Renders the DFM results tree"""
        expanded_rules = set()
        for i in range(self.model.rowCount()):
            idx = self.model.index(i, 0)
            if self.form.tvResults.isExpanded(idx):
                expanded_rules.add(self.model.item(i).text().split("\x00")[0])

        self.model.clear()
        self.form.tvResults.setItemDelegate(DFMTreeDelegate())
        root = self.model.invisibleRootItem()

        for rule_id, findings in grouped_data.items():
            error_count = sum(1 for f in findings if not f.ignore and f.severity == Severity.ERROR)
            warning_count = sum(
                1 for f in findings if not f.ignore and f.severity == Severity.WARNING
            )
            active_count = sum(1 for f in findings if not f.ignore)
            all_ignored = active_count == 0

            # Use success icon and color if all findings are ignored
            severity = findings[0].severity
            icon = self._get_icon(Severity.SUCCESS) if all_ignored else self._get_icon(severity)
            color = "#639922" if all_ignored else _severity_color(severity)

            rule_item = QStandardItem()
            rule_item.setEditable(False)
            rule_item.setData(findings, QtCore.Qt.ItemDataRole.UserRole)
            rule_item.setData("rule", QtCore.Qt.ItemDataRole.UserRole + 1)
            rule_item.setData(rule_id.label, QtCore.Qt.ItemDataRole.UserRole + 2)
            rule_item.setData(str(active_count), QtCore.Qt.ItemDataRole.UserRole + 3)
            rule_item.setData(color, QtCore.Qt.ItemDataRole.UserRole + 4)
            rule_item.setData(error_count, QtCore.Qt.ItemDataRole.UserRole + 6)
            rule_item.setData(warning_count, QtCore.Qt.ItemDataRole.UserRole + 7)
            rule_item.setIcon(icon)

            for finding in findings:
                name = face_namer_func(finding.failing_geometry[0])
                child = QStandardItem()
                child.setEditable(False)
                child.setData(finding, QtCore.Qt.ItemDataRole.UserRole)
                child.setData("finding", QtCore.Qt.ItemDataRole.UserRole + 1)
                child.setData(name, QtCore.Qt.ItemDataRole.UserRole + 2)
                child.setData(finding.overview, QtCore.Qt.ItemDataRole.UserRole + 3)
                child.setData(
                    _severity_color(finding.severity), QtCore.Qt.ItemDataRole.UserRole + 4
                )
                child.setData(finding.ignore, QtCore.Qt.ItemDataRole.UserRole + 5)
                child.setIcon(self._get_icon(finding.severity))
                rule_item.appendRow(child)

            root.appendRow(rule_item)
            if rule_id.label in expanded_rules:
                self.form.tvResults.setExpanded(rule_item.index(), True)

        for rule in all_process_rules:
            if rule not in grouped_data:
                pass_item = QStandardItem()
                pass_item.setEditable(False)
                pass_item.setData("rule", QtCore.Qt.ItemDataRole.UserRole + 1)
                pass_item.setData(rule.label, QtCore.Qt.ItemDataRole.UserRole + 2)
                pass_item.setData("0", QtCore.Qt.ItemDataRole.UserRole + 3)
                pass_item.setData("#639922", QtCore.Qt.ItemDataRole.UserRole + 4)
                pass_item.setIcon(QtGui.QIcon(":/icons/dfm_success.svg"))
                root.appendRow(pass_item)

    def _get_icon(self, severity: Severity) -> QtGui.QIcon:
        """Returns a severity circle icon for the given severity level."""
        icon_map = {
            Severity.ERROR: ":/icons/dfm_error.svg",
            Severity.WARNING: ":/icons/dfm_warning.svg",
            Severity.INFO: ":/icons/dfm_info.svg",
        }
        path = icon_map.get(severity, ":/icons/dfm_success.svg")
        return QtGui.QIcon(path)

    def _handle_click(self, index: QtCore.QModelIndex):
        item = self.model.itemFromIndex(index)
        if item and self.on_row_selected:
            data = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if data:
                self.on_row_selected(data)

    def _handle_double_click(self, index: QtCore.QModelIndex):
        item = self.model.itemFromIndex(index)
        if item:
            data = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if isinstance(data, CheckResult) and self.on_row_double_clicked:
                self.on_row_double_clicked(data)

    def _handle_export_btn(self):
        if self.on_export_clicked:
            self.on_export_clicked()

    def _show_context_menu(self, point: QtCore.QPoint):
        index = self.form.tvResults.indexAt(point)
        item = self.model.itemFromIndex(index)
        if not item:
            return

        data = item.data(QtCore.Qt.ItemDataRole.UserRole)
        menu = QtWidgets.QMenu()

        if isinstance(data, list):
            # Rule-level context menu
            findings: list[CheckResult] = data
            active = [f for f in findings if not f.ignore]
            ignored = [f for f in findings if f.ignore]

            zoom_action = menu.addAction("Zoom to All Findings")
            menu.addSeparator()

            if active:
                ignore_all = menu.addAction(f"Ignore All ({len(active)})")
                ignore_all.triggered.connect(
                    lambda: self.on_toggle_ignore_all(active) if self.on_toggle_ignore_all else None
                )

            if ignored:
                restore_all = menu.addAction(f"Restore All ({len(ignored)})")
                restore_all.triggered.connect(
                    lambda: self.on_toggle_ignore_all(ignored)
                    if self.on_toggle_ignore_all
                    else None
                )

            menu.addSeparator()
            copy_action = menu.addAction("Copy Summary")

            zoom_action.triggered.connect(
                lambda: self.on_zoom_to_rule(findings) if self.on_zoom_to_rule else None
            )
            copy_action.triggered.connect(lambda: self._copy_rule_summary(findings))

        elif isinstance(data, CheckResult):
            # Issue-level context menu
            finding: CheckResult = data

            zoom_action = menu.addAction("Zoom to Face")
            zoom_action.triggered.connect(
                lambda: self.on_row_double_clicked(finding) if self.on_row_double_clicked else None
            )

            menu.addSeparator()

            ignore_txt = "Restore" if finding.ignore else "Ignore"
            ignore_action = menu.addAction(ignore_txt)
            ignore_action.triggered.connect(
                lambda: self.on_toggle_ignore(finding) if self.on_toggle_ignore else None
            )

            menu.addSeparator()
            copy_action = menu.addAction("Copy Details")
            copy_action.triggered.connect(lambda: self._copy_issue_details(finding))

        else:
            return

        menu.exec(self.form.tvResults.viewport().mapToGlobal(point))

    def _copy_rule_summary(self, findings: list[CheckResult]):
        """Copies a plain-text summary of all findings under a rule to the clipboard."""
        if not findings:
            return
        rule_label = findings[0].rule_id.label
        lines = [f"{rule_label} — {len(findings)} finding(s)"]
        for f in findings:
            status = "[ignored]" if f.ignore else f"[{f.severity.name}]"
            lines.append(f"  {status} {f.overview}")
        QtWidgets.QApplication.clipboard().setText("\n".join(lines))

    def _copy_issue_details(self, finding: CheckResult):
        """Copies full issue details to the clipboard as plain text."""
        lines = [
            f"Rule: {finding.rule_id.label}",
            f"Severity: {finding.severity.name}",
            f"Overview: {finding.overview}",
            f"Details: {finding.message}",
        ]
        if finding.value is not None:
            lines.append(f"Measured: {finding.value:.2f}{finding.unit}")
        if finding.limit is not None:
            lines.append(f"Limit: {finding.limit:.2f}{finding.unit}")
        QtWidgets.QApplication.clipboard().setText("\n".join(lines))

    def on_save_clicked(self):
        print("saved")

    def getStandardButtons(self):
        return (
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Close
        )

    def clicked(self, button):
        if button == QtWidgets.QDialogButtonBox.StandardButton.Save:
            self._save_clicked = True
            if self.on_save_clicked:
                self.on_save_clicked()

    def reject(self):
        if self.on_closed:
            self.on_closed()
        Gui.Control.closeDialog()

    def accept(self):
        if self._save_clicked:
            self._save_clicked = False
            return
        if self.on_closed:
            self.on_closed()
        Gui.Control.closeDialog()


class DFMReportModel:
    """Holds the data of a DFM report and handles grouping and verdicts."""

    STATUS_THEMES = {
        "FAILED": {"text": "Failed", "color": "#E24B4A"},
        "WARNING": {"text": "Warning", "color": "#D4900A"},
        "SUCCESSFUL": {"text": "Successful", "color": "#639922"},
    }

    def __init__(self, results: list[CheckResult], process: Process, material: str):
        self.results = results
        self.process = process
        self.material = material

    @property
    def active_results(self):
        return [r for r in self.results if not r.ignore]

    def get_grouped_results(self) -> dict:
        grouped = defaultdict(list)
        for result in self.results:
            grouped[result.rule_id].append(result)
        for rule_id in grouped:
            grouped[rule_id].sort(key=lambda x: (x.ignore, -x.severity.value))
        return dict(sorted(grouped.items(), key=lambda item: item[0].label))

    def get_verdict(self) -> tuple[str, str]:
        errors = sum(1 for r in self.active_results if r.severity == Severity.ERROR)
        warnings = sum(1 for r in self.active_results if r.severity == Severity.WARNING)

        parts = []
        if errors:
            parts.append(f"{errors} error{'s' if errors != 1 else ''}")
        if warnings:
            parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")

        details = f" ({', '.join(parts)})" if parts else ""
        if errors:
            theme = self.STATUS_THEMES["FAILED"]
        elif warnings:
            theme = self.STATUS_THEMES["WARNING"]
        else:
            theme = self.STATUS_THEMES["SUCCESSFUL"]

        return f"{theme['text']}{details}", theme["color"]

    def toggle_ignore_state(self, finding: CheckResult):
        finding.ignore = not finding.ignore


class CSVResultExporter:
    @staticmethod
    def export(filepath: str, target_label: str, model: DFMReportModel, face_namer_func: Callable):
        try:
            with open(filepath, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                verdict_text, _ = model.get_verdict()

                # Metadata
                writer.writerow(["Design", target_label])
                writer.writerow(["Process", model.process.name])
                writer.writerow(["Material", model.material])
                writer.writerow(["Verdict", verdict_text])
                writer.writerow([])

                # Column Headers
                writer.writerow(
                    ["Status", "Rule Name", "Faces", "Value", "Comparison", "Limit", "Unit"]
                )

                for result in model.results:
                    if result.ignore:
                        continue

                    faces = "; ".join([face_namer_func(f) for f in result.failing_geometry])

                    # Write issue rows
                    writer.writerow(
                        [
                            result.severity.name,
                            result.rule_id.label,
                            faces,
                            result.value if result.value is not None else "N/A",
                            result.comparison,
                            result.limit if result.limit is not None else "N/A",
                            result.unit,
                        ]
                    )

            return True
        except Exception as e:
            FreeCAD.Console.PrintError(f"Export failed: {e}\n")
            return False


class DFMAnnotation:
    """Annotation class for DFM issues in FreeCAD."""

    # Default Styles
    TEXT_COLOR = (0.95, 0.95, 0.95)
    BG_COLOR = (0.6, 0.0, 0.0)
    FONT_SIZE = 14
    DISPLAY_MODE = "Line"

    def __init__(self, name="DFM_Issue_Annotation"):
        self.name = name
        self._active_name = None

    def remove(self, doc):
        """Removes the current annotation from the document."""
        if self._active_name:
            try:
                if obj := doc.getObject(self._active_name):
                    doc.removeObject(obj.Name)
            except Exception:
                pass
            self._active_name = None

    def create(self, doc, base_pos, text_pos, text, style_cfg=None):
        """Creates a new annotation with leader lines."""
        style = style_cfg or {}
        self.remove(doc)

        label = doc.addObject("App::AnnotationLabel", self.name)
        self._active_name = label.Name

        label.LabelText = [text]
        label.BasePosition = base_pos
        label.TextPosition = text_pos

        if vo := label.ViewObject:
            vo.TextColor = style.get("text_color", self.TEXT_COLOR)
            vo.BackgroundColor = style.get("bg_color", self.BG_COLOR)

            if hasattr(vo, "ShowInTree"):
                vo.ShowInTree = False

            if "DisplayMode" in vo.PropertiesList:
                vo.DisplayMode = self.DISPLAY_MODE

            f_size = style.get("font_size", self.FONT_SIZE)
            if hasattr(vo, "FontSize"):
                vo.FontSize = f_size
            if hasattr(label, "FontSize"):
                label.FontSize = f_size

        return label


class DFMViewProvider:
    """Manages 3D visual feedback, including face highlighting and annotations."""

    OVERLAY_HIGHLIGHT_COLORS = {
        "#E24B4A": (0.886, 0.294, 0.290, 0.0),
        "#D4900A": (0.831, 0.565, 0.039, 0.0),
        "#378ADD": (0.216, 0.541, 0.867, 0.0),
        "#639922": (0.388, 0.600, 0.133, 0.0),
    }
    OVERLAY_INACTIVE_COLOR = (0.5, 0.5, 0.5, 0.7)
    OVERLAY_TRANSPARENCY = 30
    OVERLAY_NAME = "DFM_Highlight_Overlay"
    ANNOTATION_OFFSET = FreeCAD.Vector(15, 15, 15)

    def __init__(self, target_object):
        self.target_object = target_object
        self.anno = DFMAnnotation()
        self._highlighted_faces: list[str] = []
        self._original_transparency: int = target_object.ViewObject.Transparency

    def get_face_name(self, occ_face) -> str:
        """Returns the internal face name (e.g. 'Face1') for a given OCC face."""
        idx = self._get_face_index(occ_face)
        return f"Face{idx + 1}" if idx != -1 else "Unknown"

    def highlight_faces(self, topo_faces: list, color_hex: str = "#E24B4A"):
        """Shows colored face overlays. topo_faces can be a flat list (single color)
        or a list of (face, color_hex) tuples for per-face coloring."""
        self._current_color_hex = color_hex
        self.anno.remove(FreeCAD.ActiveDocument)
        Gui.Selection.clearSelection()

        if topo_faces and isinstance(topo_faces[0], tuple):
            face_color_pairs = topo_faces
        else:
            face_color_pairs = [(f, color_hex) for f in topo_faces]

        index_color_map: dict[int, str] = {}
        for face, hex_col in face_color_pairs:
            idx = self._get_face_index(face)
            if idx != -1:
                # If a face appears under multiple severities, error wins over warning
                existing = index_color_map.get(idx)
                if existing is None or hex_col == "#E24B4A":
                    index_color_map[idx] = hex_col

        self._highlighted_faces = [f"Face{i + 1}" for i in index_color_map]

        if not index_color_map:
            self._remove_overlay()
            self.target_object.ViewObject.Visibility = True
            self.target_object.ViewObject.Transparency = self._original_transparency
            return

        self.target_object.ViewObject.Visibility = True

        self._update_overlay(index_color_map)

        if self._highlighted_faces:
            overlay = self._get_overlay()
            if overlay:
                Gui.Selection.addSelection(overlay, self._highlighted_faces[0])
                Gui.Selection.clearSelection()

    def annotate_issue(self, topo_faces: list, text: str, color_hex: str = "#E24B4A"):
        """Adds a 3D label pointing to a point guaranteed to lie on the face surface."""
        if not topo_faces:
            return

        idx = self._get_face_index(topo_faces[0])
        if idx == -1:
            return

        face = self.target_object.Shape.Faces[idx]
        base_pos = self._find_on_surface_point(face)

        h = color_hex.lstrip("#")
        r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))

        self.anno.create(
            doc=FreeCAD.ActiveDocument,
            base_pos=base_pos,
            text_pos=base_pos.add(self.ANNOTATION_OFFSET),
            text=text,
            style_cfg={"bg_color": (r, g, b)},
        )
        FreeCAD.ActiveDocument.recompute()  # type: ignore

    def _update_overlay(self, index_color_map: dict[int, str]):
        """Creates or updates the overlay with per-face colors from an index→hex map."""
        doc = FreeCAD.ActiveDocument
        overlay = self._get_overlay()

        if overlay is None:
            overlay = doc.addObject("Part::Feature", self.OVERLAY_NAME)  # type: ignore

        overlay.Shape = self.target_object.Shape.copy()

        self.target_object.ViewObject.Visibility = False

        vo = overlay.ViewObject
        if vo is None:
            return

        if hasattr(vo, "ShowInTree"):
            vo.ShowInTree = False

        vo.Transparency = self.OVERLAY_TRANSPARENCY
        vo.LineWidth = 0
        vo.PointSize = 0

        def hex_to_rgba(hex_color: str) -> tuple:
            h = hex_color.lstrip("#")
            r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
            return (r, g, b, 0.0)

        num_faces = len(self.target_object.Shape.Faces)
        colors = [
            hex_to_rgba(index_color_map[i]) if i in index_color_map else self.OVERLAY_INACTIVE_COLOR
            for i in range(num_faces)
        ]

        vo.DiffuseColor = colors
        vo.Transparency = self.OVERLAY_TRANSPARENCY
        doc.recompute()  # type: ignore

    def _find_on_surface_point(self, face) -> FreeCAD.Vector:
        """Returns a point guaranteed to lie on the face surface.

        Tries the UV midpoint first. If that falls in a hole or outside the face
        boundary (common on annular and trimmed faces), samples a grid until a
        valid point is found. Falls back to CenterOfMass if nothing works.
        """
        u0, u1, v0, v1 = face.ParameterRange

        candidates = [(0.5, 0.5)]
        steps = 5
        for i in range(steps):
            for j in range(steps):
                candidates.append((i / (steps - 1), j / (steps - 1)))

        for u_norm, v_norm in candidates:
            u = u0 + u_norm * (u1 - u0)
            v = v0 + v_norm * (v1 - v0)
            try:
                pt = face.valueAt(u, v)
                if face.isInside(pt, 1e-3, True):
                    return pt
            except Exception:
                continue

        return face.CenterOfMass  # fallback

    def zoom_to_selection(self):
        """Focuses the 3D camera on the currently highlighted faces."""
        overlay = self._get_overlay()
        if self._highlighted_faces and overlay and Gui.activeView():
            Gui.Selection.addSelection(overlay, self._highlighted_faces)
            Gui.SendMsgToActiveView("ViewSelection")
            Gui.SendMsgToActiveView("AlignToSelection")
            Gui.Selection.clearSelection()

    def restore(self):
        """Removes the overlay and annotation, returning the viewport to its original state."""
        self.anno.remove(FreeCAD.ActiveDocument)
        self._remove_overlay()
        self.target_object.ViewObject.Visibility = True

    def _remove_overlay(self):
        """Removes the overlay object from the document if it exists."""
        doc = FreeCAD.ActiveDocument
        if self.OVERLAY_NAME:
            try:
                obj = doc.getObject(self.OVERLAY_NAME)  # type: ignore
                if obj:
                    doc.removeObject(self.OVERLAY_NAME)  # type: ignore
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"Failed to remove DFM overlay: {e}\n")
        self._highlighted_faces = []

    def _get_overlay(self):
        """Returns the live overlay document object, or None if it doesn't exist."""
        if self.OVERLAY_NAME:
            return FreeCAD.ActiveDocument.getObject(self.OVERLAY_NAME)  # type: ignore
        return None

    def _get_face_index(self, occ_face) -> int:
        """Finds the index of an OCC face within the target object's shape."""
        for i, f in enumerate(self.target_object.Shape.Faces):
            if Part.__toPythonOCC__(f).IsSame(occ_face):
                return i
        return -1


class TaskResultsPresenter:
    """Presenter for the TaskResults view. Handles UI updates and event handling."""

    def __init__(self, view: TaskResults, model: DFMReportModel, bridge: DFMViewProvider):
        self.view = view
        self.model = model
        self.bridge = bridge

        self.view.on_row_selected = self.handle_selection
        self.view.on_row_double_clicked = self.handle_zoom
        self.view.on_toggle_ignore = self.handle_ignore
        self.view.on_export_clicked = self.handle_export
        self.view.on_closed = self.handle_cleanup
        self.view.on_save_clicked = self.handle_save
        self.view.on_toggle_ignore_all = self.handle_ignore_all
        self.view.on_zoom_to_rule = self.handle_zoom_to_rule

        self.view.adjust_details_height()

        self.refresh_ui()
        Gui.Control.showDialog(self.view)

    def refresh_ui(self):
        self.view.form.leTarget.setText(self.bridge.target_object.Label)
        self.view.form.leProcess.setText(self.model.process.name)
        self.view.form.leMaterial.setText(self.model.material)
        self.view.form.leTarget.setCursorPosition(0)
        self.view.form.leProcess.setCursorPosition(0)
        self.view.form.leMaterial.setCursorPosition(0)

        text, color = self.model.get_verdict()
        self.view.form.leVerdict.setText(text)
        self.view.form.leVerdict.setStyleSheet(f"color: {color}; font-weight: bold;")

        self.view.render_tree(
            self.model.get_grouped_results(),
            self.bridge.get_face_name,
            self.model.process.active_rules,
        )

        if not self.model.active_results:
            self.view.form.tbDetails.setHtml(
                "<b>No issues found.</b><br>"
                f"This design passed all active checks for <i>{self.model.process.name}</i> "
                f"with material <i>{self.model.material}</i>. "
                "It meets the manufacturing requirements as configured."
            )
            self.view.adjust_details_height()

    def handle_selection(self, data: CheckResult | list[CheckResult]):
        Gui.Selection.clearSelection()
        if isinstance(data, list):
            rule_name = data[0].rule_id.label if data else "Rule"
            self.view.form.tbDetails.setHtml(f"<b>Rule: {rule_name}</b><br>Showing all findings.")

            # Build per-face colour pairs so each face uses its finding's severity colour
            face_color_pairs = [
                (face, _severity_color(r.severity))
                for r in data
                if not r.ignore
                for face in r.failing_geometry
            ]
            self.bridge.highlight_faces(face_color_pairs)
        elif isinstance(data, CheckResult):
            overview = html.escape(data.overview)
            color = _severity_color(data.severity)
            icon = self.view._get_icon(data.severity)
            icon_html = _icon_to_html(icon, size=16)
            self.view.form.tbDetails.setHtml(
                f"<table cellspacing='0' cellpadding='0'><tr>"
                f"<td valign='middle' style='padding-right:4px'>{icon_html}</td>"
                f"<td valign='middle'><b>{overview}</b></td>"
                f"</tr></table>"
                f"<p style='margin-top:4px'>{data.message}</p>"
            )
            self.bridge.highlight_faces(data.failing_geometry, color)
            self.bridge.annotate_issue(data.failing_geometry, data.overview, color)
        self.view.adjust_details_height()

    def handle_zoom(self, result: CheckResult):
        self.handle_selection(result)
        self.bridge.zoom_to_selection()

    def handle_ignore(self, result: CheckResult):
        self.model.toggle_ignore_state(result)
        self.refresh_ui()

    def handle_export(self):
        path, _ = QFileDialog.getSaveFileName(
            self.view.form, "Export CSV", "", "CSV Files (*.csv);;All Files (*)"
        )

        if not path:
            return

        if not path.lower().endswith(".csv"):
            path += ".csv"

        if CSVResultExporter.export(
            path, self.bridge.target_object.Label, self.model, self.bridge.get_face_name
        ):
            QMessageBox.information(self.view.form, "Done", "Export Successful")

    def handle_cleanup(self):
        self.bridge.restore()
        Gui.Selection.addSelection(self.bridge.target_object)
        Gui.Selection.clearSelection()

    def handle_save(self):
        QMessageBox.information(
            self.view.form, "Not Implemented", "Save Results is not yet implemented."
        )

    def handle_ignore_all(self, findings: list[CheckResult]):
        for finding in findings:
            self.model.toggle_ignore_state(finding)
        self.refresh_ui()

    def handle_zoom_to_rule(self, findings: list[CheckResult]):
        all_faces = [f for r in findings if not r.ignore for f in r.failing_geometry]
        rule_name = findings[0].rule_id.label if findings else "Rule"
        self.view.form.tbDetails.setHtml(f"<b>Rule: {rule_name}</b><br>Showing all findings.")
        self.bridge.highlight_faces(all_faces)
        self.bridge.zoom_to_selection()
        self.view.adjust_details_height()


def _icon_to_html(icon: QtGui.QIcon, size: int = 16) -> str:
    pixmap = icon.pixmap(size, size)
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    b64 = base64.b64encode(buffer.data().data()).decode()
    img = f"<img src='data:image/png;base64,{b64}' width='{size}' height='{size}'/>"
    return img
