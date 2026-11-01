# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Preferences > DFM > Reports: the defaults the PDF report starts from."""

from pathlib import Path

from PySide6 import QtGui, QtWidgets

from ...app.prefs import dfm_params
from .settings import CHOICES, ReportSettings
from .template import (
    TEMPLATE_FOLDER_PREF,
    all_templates,
    default_template_folder,
)

# (field, kind, label, extra). Kinds: line, check, combo, spin, file.
_GROUPS = [
    ("Template", [
        ("template", "template", "Default template", None),
        ("prepared_by", "line", "Prepared by", None),
    ]),
    ("Branding", [
        ("company_name", "line", "Company", None),
        ("company_logo", "file", "Logo", "Images (*.svg *.png *.jpg *.jpeg)"),
        ("show_company_logo", "check", "Show the company logo in the header", None),
        ("show_dfm_logo", "check", "Show the DFM workbench logo in the footer", None),
        ("show_freecad_logo", "check", "Show the FreeCAD logo in the footer", None),
    ]),
    ("Content", [
        ("title", "line", "Title", None),
        ("overview_image", "check", "Overview image on the cover", None),
        ("design_image", "check", "Also show the model as designed, next to the overview", None),
        ("overview_view", "combo", "Overview view", None),
        ("summary_table", "check", "Summary table by criticality", None),
        ("include_errors", "check", "Include errors", None),
        ("include_warnings", "check", "Include warnings", None),
        ("include_passed", "check", "Include passed rules", None),
        ("include_ignored", "check", "Include ignored findings", None),
        ("group_by", "combo", "Group by", None),
        ("rule_diagrams", "check", "Rule diagrams", None),
        ("feedback_messages", "check", "Feedback messages", None),
        ("target_and_limit", "check", "Target and limit", None),
        ("list_at_most", "spin", "List at most, per rule", (0, 1000)),
        ("findings_appendix", "check", "Full findings table as appendix", None),
        ("settings_appendix", "check", "Process and material settings appendix", None),
    ]),
    ("Viewport images", [
        ("image_mode", "combo", "Images", None),
        ("image_findings", "combo", "Findings with images", None),
        ("images_per_rule", "spin", "At most, per rule", (1, 200)),
        ("framing", "combo", "Framing", None),
        ("view_direction", "combo", "View direction", None),
        ("fade_rest", "check", "Show the rest of the model faded", None),
        ("see_through", "spin", "See-through part", (0, 90, " %")),
        ("hide_markers", "check", "Hide viewport markers and annotations", None),
        ("resolution", "combo", "Resolution", None),
        ("background", "combo", "Background", None),
    ]),
    ("File", [
        ("file_name", "line", "File name pattern", None),
        ("open_after", "check", "Open the PDF after exporting", None),
    ]),
]

_TOOLTIPS = {
    "see_through": "Transparency for images that show several findings (the cover overview\n"
                   "and one image per rule), so findings on the far side still show.",
    "title": "You can use {model}, {document}, {project}, {material} and {date}.",
    "file_name": "You can use {document}, {model}, {material}, {process}, {project},\n"
                 "{revision} and {date}. \".pdf\" is added for you.",
    "images_per_rule": "With one image per finding: the most images any one rule gets",
    "list_at_most": "Findings listed under each rule. The rest go in the appendix.",
    "findings_appendix": "Added when a rule has more findings than are listed in its section",
}


class ReportsSettingsPanel(QtWidgets.QWidget):
    """Every report option, laid out like the other DFM preference pages."""

    def __init__(self):
        super().__init__()
        self._widgets: dict[str, QtWidgets.QWidget] = {}
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("Report Settings")
        font = title.font()
        font.setPointSize(16)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        for group_title, fields in _GROUPS:
            box = QtWidgets.QGroupBox(group_title)
            grid = QtWidgets.QGridLayout(box)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            for row, (name, kind, label, extra) in enumerate(fields):
                widget = self._create(name, kind, label, extra)
                if name in _TOOLTIPS:
                    widget.setToolTip(_TOOLTIPS[name])
                if kind == "check":
                    grid.addWidget(widget, row, 0, 1, 2)
                else:
                    grid.addWidget(QtWidgets.QLabel(label), row, 0)
                    grid.addWidget(widget, row, 1)
                if group_title == "Template" and row == 0:
                    grid.addWidget(QtWidgets.QLabel("Template folder"), len(fields), 0)
                    self.folder = QtWidgets.QLineEdit()
                    self.folder.setPlaceholderText(str(default_template_folder()))
                    self.folder.setToolTip("Report templates (YAML files) are read from this folder")
                    grid.addWidget(self._with_browse(self.folder, folder=True), len(fields), 1)
            layout.addWidget(box)
        layout.addStretch()

    def _create(self, name: str, kind: str, label: str, extra) -> QtWidgets.QWidget:
        if kind == "check":
            w = QtWidgets.QCheckBox(label)
        elif kind == "combo":
            w = QtWidgets.QComboBox()
            for value, text in CHOICES[name]:
                w.addItem(text, value)
        elif kind == "template":
            w = QtWidgets.QComboBox()
        elif kind == "spin":
            w = QtWidgets.QSpinBox()
            w.setRange(extra[0], extra[1])
            if len(extra) > 2:
                w.setSuffix(extra[2])
        elif kind == "file":
            edit = QtWidgets.QLineEdit()
            self._widgets[name] = edit
            return self._with_browse(edit, file_filter=extra)
        else:
            w = QtWidgets.QLineEdit()
        if isinstance(w, QtWidgets.QComboBox):
            w.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self._widgets[name] = w
        return w

    def _with_browse(self, edit: QtWidgets.QLineEdit, folder: bool = False,
                     file_filter: str = "") -> QtWidgets.QWidget:
        holder = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        button = QtWidgets.QToolButton()
        button.setText("…")

        def browse():
            start = edit.text() or edit.placeholderText()
            if folder:
                path = QtWidgets.QFileDialog.getExistingDirectory(self, "Template folder", start)
            else:
                path, _ = QtWidgets.QFileDialog.getOpenFileName(
                    self, "Choose a company logo", str(Path(start).expanduser().parent) if start else "",
                    file_filter)
            if path:
                edit.setText(path)

        button.clicked.connect(browse)
        row.addWidget(button)
        return holder

    # ------------------------------------------------------------------

    def load(self, params) -> None:
        settings = ReportSettings.load(params)
        self.folder.setText(params.GetString(TEMPLATE_FOLDER_PREF, ""))
        templates = self._widgets["template"]
        templates.clear()
        names = list(all_templates())
        if settings.template not in names:
            names.append(settings.template)
        templates.addItems(names)
        for name, w in self._widgets.items():
            value = getattr(settings, name)
            if isinstance(w, QtWidgets.QCheckBox):
                w.setChecked(bool(value))
            elif isinstance(w, QtWidgets.QSpinBox):
                w.setValue(int(value))
            elif isinstance(w, QtWidgets.QComboBox):
                i = w.findData(value) if name != "template" else w.findText(value)
                w.setCurrentIndex(max(i, 0))
            else:
                w.setText(str(value))

    def save(self, params) -> None:
        params.SetString(TEMPLATE_FOLDER_PREF, self.folder.text().strip())
        settings = ReportSettings.load(params)
        for name, w in self._widgets.items():
            if isinstance(w, QtWidgets.QCheckBox):
                value = w.isChecked()
            elif isinstance(w, QtWidgets.QSpinBox):
                value = w.value()
            elif isinstance(w, QtWidgets.QComboBox):
                value = w.currentText() if name == "template" else w.currentData()
            else:
                value = w.text().strip()
            setattr(settings, name, value)
        settings.save(params)


class DFMPreferencesReports:
    """The Reports page under Preferences > DFM."""

    def __init__(self):
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("Reports")
        self.form.setWindowIcon(QtGui.QIcon(":/icons/dfm_analysis.svg"))
        self.panel = ReportsSettingsPanel()
        QtWidgets.QVBoxLayout(self.form).addWidget(self.panel)

    def loadSettings(self):
        self.panel.load(dfm_params())

    def saveSettings(self):
        self.panel.save(dfm_params())
