# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from dataclasses import dataclass, field
from typing import Any, Optional, TypeVar

from PySide6 import QtGui, QtWidgets, QtCore

import FreeCADGui as Gui  # type: ignore

from ..app.prefs import dfm_params
from .utils.widgets import ToleranceSpinBox

_PREF_PATH = "Mod/DFM"


_W = TypeVar("_W", bound=QtWidgets.QWidget)


def _pref(widget: _W, entry: str) -> _W:
    """Sets FreeCAD preference properties on a widget so reset works natively."""
    widget.setProperty("prefPath", _PREF_PATH)
    widget.setProperty("prefEntry", entry)
    return widget


@dataclass
class IntField:
    key: str
    label: str
    default: int
    min: int = 0
    max: int = 100
    suffix: str = ""
    tooltip: str = ""


@dataclass
class FloatField:
    key: str
    label: str
    default: float
    min: float = 0.0
    max: float = 1.0
    step: float = 0.01
    decimals: int = 3
    suffix: str = ""
    tooltip: str = ""


@dataclass
class BoolField:
    key: str
    label: str
    default: bool = False
    tooltip: str = ""


@dataclass
class ToleranceField:
    key: str
    label: str
    default: float = 1e-3
    suffix: str = ""
    tooltip: str = ""


@dataclass
class FieldGroup:
    title: str
    fields: list = field(default_factory=list)


class AnalyzerPanel(QtWidgets.QWidget):
    """
    Subclass and set `title` and `groups` to get a full preference panel
    with zero widget or layout code.

    Cross-field validation can be added by overriding `connect_signals`.
    """

    title: str = ""
    groups: list[FieldGroup] = []

    def __init__(self):
        super().__init__()
        self._widgets: dict[str, QtWidgets.QWidget] = {}
        self._defaults: dict[str, Any] = {}
        self._build_ui()
        self.connect_signals()

    def connect_signals(self):
        """Override to add cross-field validation."""
        pass

    def widget(self, key: str) -> QtWidgets.QWidget:
        return self._widgets[key]

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        lbl = QtWidgets.QLabel(self.title)
        font = lbl.font()
        font.setPointSize(16)
        font.setBold(True)
        lbl.setFont(font)
        layout.addWidget(lbl)

        for group in self.groups:
            layout.addWidget(self._build_group(group))

        layout.addStretch()

    def _build_group(self, group: FieldGroup) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox(group.title)
        grid = QtWidgets.QGridLayout(box)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        for row, f in enumerate(group.fields):
            w = self._create_widget(f)
            self._widgets[f.key] = w
            self._defaults[f.key] = f.default

            if isinstance(f, BoolField):
                grid.addWidget(w, row, 0, 1, 2)
            else:
                label = QtWidgets.QLabel(f.label)
                grid.addWidget(label, row, 0)
                grid.addWidget(w, row, 1)

        return box

    def _create_widget(self, f) -> QtWidgets.QWidget:
        if isinstance(f, IntField):
            w = _pref(QtWidgets.QSpinBox(), f.key)
            w.setRange(f.min, f.max)
            if f.suffix:
                w.setSuffix(f.suffix)
            if f.tooltip:
                w.setToolTip(f.tooltip)
            return w

        if isinstance(f, FloatField):
            w = _pref(QtWidgets.QDoubleSpinBox(), f.key)
            w.setRange(f.min, f.max)
            w.setSingleStep(f.step)
            w.setDecimals(f.decimals)
            if f.suffix:
                w.setSuffix(f.suffix)
            if f.tooltip:
                w.setToolTip(f.tooltip)
            return w

        if isinstance(f, BoolField):
            w = _pref(QtWidgets.QCheckBox(f.label), f.key)
            if f.tooltip:
                w.setToolTip(f.tooltip)
            return w

        if isinstance(f, ToleranceField):
            w = _pref(ToleranceSpinBox(), f.key)
            if f.tooltip:
                w.setToolTip(f.tooltip)
            return w

        raise TypeError(f"Unknown field type: {type(f)}")

    def load(self, params):
        for key, widget in self._widgets.items():
            default = self._defaults[key]
            if isinstance(widget, QtWidgets.QCheckBox):
                widget.setChecked(params.GetBool(key, default))
            elif isinstance(widget, QtWidgets.QDoubleSpinBox):
                widget.setValue(params.GetFloat(key, default))
            elif isinstance(widget, ToleranceSpinBox):
                widget.setValue(params.GetFloat(key, default))
            elif isinstance(widget, QtWidgets.QSpinBox):
                widget.setValue(params.GetInt(key, default))

    def save(self, params):
        for key, widget in self._widgets.items():
            if isinstance(widget, QtWidgets.QCheckBox):
                params.SetBool(key, widget.isChecked())
            elif isinstance(widget, QtWidgets.QDoubleSpinBox):
                params.SetFloat(key, widget.value())
            elif isinstance(widget, ToleranceSpinBox):
                params.SetFloat(key, widget.value())
            elif isinstance(widget, QtWidgets.QSpinBox):
                params.SetInt(key, widget.value())


# =============================================================================


class SphereThicknessPanel(AnalyzerPanel):
    title = "Sphere Thickness"
    groups = [
        FieldGroup(
            "Sampling Settings",
            [
                IntField(
                    "SphereMinSamples",
                    "Min sampling density",
                    default=5,
                    min=2,
                    max=99,
                    suffix="²",
                ),
                IntField(
                    "SphereMaxSamples",
                    "Max sampling density",
                    default=10,
                    min=3,
                    max=100,
                    suffix="²",
                ),
                FloatField(
                    "SphereMargin",
                    "Boundary margin",
                    default=0.01,
                    max=0.495,
                    step=0.005,
                    suffix="%",
                    tooltip="UV boundary margin. Prevents sampling too close to face edges.",
                ),
            ],
        ),
        FieldGroup(
            "Algorithm Settings",
            [
                BoolField(
                    "SphereMultiThreaded",
                    "Enable multithreading",
                    tooltip="May improve performance on complex shapes but can cause overhead on simple ones.",
                ),
                IntField(
                    "SphereMaxShrinkIters",
                    "Max shrink iterations",
                    default=10,
                    min=1,
                    max=100,
                    tooltip="Sets the maximum iterations the sphere can shrink at a tested point on a face during analysis.",
                ),
                ToleranceField(
                    "SphereIntersectorTol",
                    "Ray intersector tolerance",
                    tooltip="Sets the load tolerance of the shape intersector.",
                ),
                ToleranceField(
                    "SphereDeflection",
                    "Extrema deflection",
                    default=1e-3,
                    tooltip="Sets the deflection tolerance for the BRepExtrema distance tool.",
                ),
            ],
        ),
    ]

    def connect_signals(self):
        min_w: QtWidgets.QSpinBox = self.widget("SphereMinSamples")  # type: ignore
        max_w: QtWidgets.QSpinBox = self.widget("SphereMaxSamples")  # type: ignore
        min_w.valueChanged.connect(lambda v: max_w.setMinimum(v + 1))
        max_w.valueChanged.connect(lambda v: min_w.setMaximum(v - 1))


class RayThicknessPanel(AnalyzerPanel):
    title = "Ray Thickness"
    groups = [
        FieldGroup(
            "Sampling Settings",
            [
                IntField(
                    "RayMinSamples",
                    "Min sampling density",
                    default=5,
                    min=2,
                    max=99,
                    suffix="²",
                ),
                IntField(
                    "RayMaxSamples",
                    "Max sampling density",
                    default=10,
                    min=3,
                    max=100,
                    suffix="²",
                ),
                FloatField(
                    "RayMargin",
                    "Boundary margin",
                    default=0.0,
                    max=0.495,
                    step=0.005,
                    suffix="%",
                    tooltip="UV boundary margin. Prevents sampling too close to face edges.",
                ),
            ],
        ),
        FieldGroup(
            "Algorithm Settings",
            [
                ToleranceField(
                    "RayIntersectorTol",
                    "Ray intersector tolerance",
                    tooltip="Sets the load tolerance of the shape intersector.",
                ),
                FloatField(
                    "RayNormalCone",
                    "Secondary hit normal cone",
                    default=5.0,
                    max=30.0,
                    step=0.01,
                    decimals=2,
                    suffix="°",
                    tooltip="Controls how strict the face must be roughly perpendicular to the ray for it to be considered a valid thickness.",
                ),
                IntField(
                    "RaySeedCoverageThreshold",
                    "Seed coverage threshold",
                    default=50,
                    min=0,
                    max=100,
                    suffix="%",
                    tooltip="Controls the percentage of seeds required before the analyzer skips a face.",
                ),
            ],
        ),
    ]

    def connect_signals(self):
        min_w: QtWidgets.QSpinBox = self.widget("RayMinSamples")  # type: ignore
        max_w: QtWidgets.QSpinBox = self.widget("RayMaxSamples")  # type: ignore
        min_w.valueChanged.connect(lambda v: max_w.setMinimum(v + 1))
        max_w.valueChanged.connect(lambda v: min_w.setMaximum(v - 1))


# =============================================================================


class BridgeSpanPanel(AnalyzerPanel):
    title = "Bridge Span"
    groups = [
        FieldGroup(
            "Detection Settings",
            [
                FloatField(
                    "BridgeHorizontalTolerance",
                    "Horizontal tolerance",
                    default=1.0,
                    max=10.0,
                    step=0.1,
                    decimals=2,
                    suffix="°",
                    tooltip="Maximum angle from horizontal (degrees) for a face to be "
                    "considered a bridge candidate.",
                ),
                FloatField(
                    "BridgeBedTolerance",
                    "Bed contact tolerance",
                    default=0.5,
                    max=5.0,
                    step=0.05,
                    suffix=" mm",
                    tooltip="How close to the lowest point of the model a face must sit to be "
                    "considered resting on the bed, and so exempt from bridging.",
                ),
                FloatField(
                    "BridgeProbeDepth",
                    "Probe depth",
                    default=0.01,
                    max=1.0,
                    step=0.005,
                    suffix=" mm",
                    tooltip="Distance below the face to probe for solid material when "
                    "detecting supported edges.",
                ),
            ],
        ),
        FieldGroup(
            "Algorithm Settings",
            [
                ToleranceField(
                    "BridgeClassifierTol",
                    "Solid classifier tolerance",
                    default=1e-6,
                    tooltip="Sets the tolerance of the inside/outside test used to find walls.",
                ),
            ],
        ),
    ]


class DraftAnglePanel(AnalyzerPanel):
    title = "Draft Angle"
    groups = [
        FieldGroup(
            "Sampling Settings",
            [
                IntField(
                    "DraftSamples",
                    "Sampling density",
                    default=20,
                    min=3,
                    max=100,
                    suffix="²",
                    tooltip="Number of UV samples per axis for curved faces.",
                ),
                FloatField(
                    "DraftMargin",
                    "Boundary margin",
                    default=0.01,
                    max=0.495,
                    step=0.005,
                    suffix="%",
                    tooltip="UV boundary margin. Prevents sampling too close to face edges.",
                ),
            ],
        ),
        FieldGroup(
            "Algorithm Settings",
            [
                ToleranceField(
                    "DraftClassifierTol",
                    "Solid classifier tolerance",
                    default=1e-6,
                    tooltip="Tolerance for the mold-side classifier that determines "
                    "core vs. cavity faces.",
                ),
            ],
        ),
    ]


class OverhangPanel(AnalyzerPanel):
    title = "Overhang"
    groups = [
        FieldGroup(
            "Sampling Settings",
            [
                IntField(
                    "OverhangSamples",
                    "Sampling density",
                    default=15,
                    min=3,
                    max=100,
                    suffix="²",
                    tooltip="Number of UV samples per axis for curved faces.",
                ),
                FloatField(
                    "OverhangMargin",
                    "Boundary margin",
                    default=0.01,
                    max=0.495,
                    step=0.005,
                    suffix="%",
                    tooltip="UV boundary margin. Prevents sampling too close to face edges.",
                ),
            ],
        ),
        FieldGroup(
            "Detection Settings",
            [
                FloatField(
                    "OverhangBedTolerance",
                    "Bed contact tolerance",
                    default=0.5,
                    max=5.0,
                    step=0.05,
                    suffix=" mm",
                    tooltip="Height tolerance for considering a face to be resting "
                    "on the print bed.",
                ),
            ],
        ),
    ]


class UndercutPanel(AnalyzerPanel):
    title = "Undercut"
    groups = [
        FieldGroup(
            "Sampling Settings",
            [
                IntField(
                    "UndercutSamples",
                    "Sampling density",
                    default=10,
                    min=3,
                    max=100,
                    suffix="²",
                    tooltip="Number of UV samples per axis for each face.",
                ),
                FloatField(
                    "UndercutMargin",
                    "Boundary margin",
                    default=0.01,
                    max=0.495,
                    step=0.005,
                    suffix="%",
                    tooltip="UV boundary margin. Prevents sampling too close to face edges.",
                ),
            ],
        ),
        FieldGroup(
            "Algorithm Settings",
            [
                ToleranceField(
                    "UndercutIntersectorTol",
                    "Ray intersector tolerance",
                    default=1e-6,
                    tooltip="Load tolerance for the shape intersector used in ray casting.",
                ),
                FloatField(
                    "UndercutSelfHitThreshold",
                    "Self-hit threshold",
                    default=0.05,
                    max=1.0,
                    step=0.01,
                    suffix=" mm",
                    tooltip="Minimum distance from the ray origin before a hit is "
                    "considered a blocking surface rather than a self-intersection.",
                ),
            ],
        ),
    ]


class SharpCornersPanel(AnalyzerPanel):
    title = "Sharp Corners"
    groups = [
        FieldGroup(
            "Detection Thresholds",
            [
                IntField(
                    "SharpExternalMinAngle",
                    "Min external corner angle",
                    default=1,
                    min=0,
                    max=90,
                    suffix="°",
                    tooltip="Edges with a dihedral angle below this value (degrees) "
                    "are not flagged as sharp external corners.",
                ),
                IntField(
                    "SharpInternalMinAngle",
                    "Min internal corner angle",
                    default=1,
                    min=0,
                    max=90,
                    suffix="°",
                    tooltip="Edges with a dihedral angle below this value (degrees) "
                    "are not flagged as sharp internal corners.",
                ),
            ],
        ),
    ]


# =============================================================================


class DFMPreferencesResults:
    """Preference page for Results panel settings (viewport markers, etc.)."""

    DEFAULT_SHOW_MARKERS = False

    def __init__(self):
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("Results")
        self.form.setWindowIcon(QtGui.QIcon(":/icons/dfm_analysis.svg"))

        self.show_markers_default = _pref(
            QtWidgets.QCheckBox("Show viewport markers by default"),
            "ResultsShowMarkersDefault",
        )
        self.show_markers_default.setToolTip(
            "When enabled, viewport markers are shown automatically\n"
            "when the results panel opens.  When disabled, markers\n"
            "must be turned on manually from the toolbar."
        )

        layout = QtWidgets.QVBoxLayout(self.form)
        group = QtWidgets.QGroupBox("Viewport Markers")
        QtWidgets.QVBoxLayout(group).addWidget(self.show_markers_default)
        layout.addWidget(group)
        layout.addStretch()

    def loadSettings(self):
        params = dfm_params()
        self.show_markers_default.setChecked(
            params.GetBool("ResultsShowMarkersDefault", self.DEFAULT_SHOW_MARKERS)
        )

    def saveSettings(self):
        params = dfm_params()
        params.SetBool("ResultsShowMarkersDefault", self.show_markers_default.isChecked())


class DFMPreferencesGeneral:
    DEFAULT_PRINT_TIMING_REPORT = False

    def __init__(self):
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("General")
        self.form.setWindowIcon(QtGui.QIcon(":/icons/dfm_analysis.svg"))

        self.print_timing_report = _pref(
            QtWidgets.QCheckBox("Print timing report"), "GeneralPrintTimingReport"
        )
        self.print_timing_report.setToolTip(
            "Prints a report to the terminal after an analysis.\n"
            "The report includes run-time for Analyzers, Checks, and total analysis run-time."
        )

        layout = QtWidgets.QVBoxLayout(self.form)
        group = QtWidgets.QGroupBox("Developer Options")
        QtWidgets.QVBoxLayout(group).addWidget(self.print_timing_report)
        layout.addWidget(group)
        layout.addStretch()

    def loadSettings(self):
        params = dfm_params()
        self.print_timing_report.setChecked(
            params.GetBool("GeneralPrintTimingReport", self.DEFAULT_PRINT_TIMING_REPORT)
        )

    def saveSettings(self):
        params = dfm_params()
        params.SetBool("GeneralPrintTimingReport", self.print_timing_report.isChecked())


class DFMPreferencesAnalyzers:
    def __init__(self):
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("Analyzers")
        self.form.setWindowIcon(QtGui.QIcon(":/icons/dfm_analysis.svg"))

        self.panels: list[AnalyzerPanel] = []

        layout = QtWidgets.QVBoxLayout(self.form)

        self.combo = QtWidgets.QComboBox()
        self.stack = QtWidgets.QStackedWidget()

        self._register(SphereThicknessPanel())
        self._register(RayThicknessPanel())
        self._register(BridgeSpanPanel())
        self._register(DraftAnglePanel())
        self._register(OverhangPanel())
        self._register(UndercutPanel())
        self._register(SharpCornersPanel())

        self.combo.currentIndexChanged.connect(self.stack.setCurrentIndex)

        layout.addWidget(self.combo)
        layout.addWidget(self.stack)

    def _register(self, panel: AnalyzerPanel):
        self.combo.addItem(panel.title)
        self.stack.addWidget(panel)
        self.panels.append(panel)

    def loadSettings(self):
        params = dfm_params()
        for p in self.panels:
            p.load(params)

    def saveSettings(self):
        params = dfm_params()
        for p in self.panels:
            p.save(params)
