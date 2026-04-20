# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from typing import Protocol

from PySide6 import QtGui, QtWidgets, QtCore

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ..gui.widgets import ToleranceSpinBox

_PREF_PATH = "Mod/DFM"


def _pref(widget: QtWidgets.QWidget, entry: str) -> QtWidgets.QWidget:
    """Sets FreeCAD preference properties on a widget so reset works natively."""
    widget.setProperty("prefPath", _PREF_PATH)
    widget.setProperty("prefEntry", entry)
    return widget


class _AnalyzerPanel(Protocol):
    def load(self, params) -> None: ...
    def save(self, params) -> None: ...


class DFMPreferencesGeneral:
    DEFAULT_PRINT_TIMING_REPORT = False

    def __init__(self):
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("General")
        self.form.setWindowIcon(QtGui.QIcon(":/icons/dfm_analysis.svg"))

        self.init_widgets()
        self.build_ui()

    def init_widgets(self) -> None:
        self.print_timing_report: QtWidgets.QCheckBox = _pref(
            QtWidgets.QCheckBox("Print timing report"), "GeneralPrintTimingReport"
        )  # type: ignore
        self.print_timing_report.setToolTip(
            "Prints a report to the terminal after an analysis. \n"
            "The report includes run-time for Analyzers, Checks, and total analysis run-time."
        )

    def build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self.form)
        layout.addWidget(self.build_dev_group())
        layout.addStretch()

    def build_dev_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Developer Options")
        layout = QtWidgets.QVBoxLayout(group)
        layout.addWidget(self.print_timing_report)
        return group

    def loadSettings(self) -> None:
        params = App.ParamGet("User parameter:BaseApp/Preferences/Mod/DFM")
        self.print_timing_report.setChecked(
            params.GetBool("GeneralPrintTimingReport", self.DEFAULT_PRINT_TIMING_REPORT)
        )

    def saveSettings(self) -> None:
        params = App.ParamGet("User parameter:BaseApp/Preferences/Mod/DFM")
        params.SetBool("GeneralPrintTimingReport", self.print_timing_report.isChecked())


class SphereThicknessPanel(QtWidgets.QWidget):
    DEFAULT_MIN_SAMPLES = 5
    DEFAULT_MAX_SAMPLES = 10
    DEFAULT_MARGIN = 0.01
    DEFAULT_MULTITHREADED = False
    DEFAULT_MAX_SHRINK_ITERS = 10
    DEFAULT_INTERSECTOR_TOL = 1e-3

    def __init__(self):
        super().__init__()
        self._init_widgets()
        self._build_ui()
        self._connect_signals()

    def _init_widgets(self) -> None:
        self.min_samples: QtWidgets.QSpinBox = _pref(QtWidgets.QSpinBox(), "SphereMinSamples")  # type: ignore
        self.min_samples.setRange(2, 99)

        self.max_samples: QtWidgets.QSpinBox = _pref(QtWidgets.QSpinBox(), "SphereMaxSamples")  # type: ignore
        self.max_samples.setRange(3, 100)

        self.margin: QtWidgets.QDoubleSpinBox = _pref(QtWidgets.QDoubleSpinBox(), "SphereMargin")  # type: ignore
        self.margin.setRange(0.0, 0.495)
        self.margin.setSingleStep(0.005)
        self.margin.setDecimals(3)
        self.margin.setToolTip("UV boundary margin. Prevents sampling too close to face edges.")

        self.multithreaded: QtWidgets.QCheckBox = _pref(  # type: ignore
            QtWidgets.QCheckBox("Enable multithreading"), "SphereMultiThreaded"
        )
        self.multithreaded.setToolTip(
            "May improve performance on complex shapes but can cause overhead on simple ones.\n"
            "\n"
            "For experts: sets SetMultiThread(True) on BRepExtrema_DistShapeShape."
        )
        self.max_shrink_iters: QtWidgets.QSpinBox = _pref(
            QtWidgets.QSpinBox(), "SphereMaxShrinkIters"
        )  # type: ignore
        self.max_shrink_iters.setRange(1, 100)
        self.max_shrink_iters.setToolTip(
            "Sets the maximum iterations the sphere can shrink at a tested point on a face during analysis."
        )
        self.intersector_tol: ToleranceSpinBox = _pref(ToleranceSpinBox(), "SphereIntersectorTol")  # type: ignore
        self.intersector_tol.setToolTip(
            "Sets the load tolerance of the shape intersector. This affects how precisely rays detect surface intersections."
        )

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        title = QtWidgets.QLabel("Sphere Thickness")
        font = title.font()
        font.setPointSize(16)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        layout.addWidget(self._build_sampling_group())
        layout.addWidget(self._build_algorithm_group())

        layout.addStretch()

    def _build_sampling_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Sampling Settings")
        form = QtWidgets.QFormLayout(group)
        form.addRow("Min sampling density", self.min_samples)
        form.addRow("Max sampling density", self.max_samples)
        form.addRow("Boundary margin", self.margin)
        return group

    def _build_algorithm_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Algorithm Settings")
        form = QtWidgets.QFormLayout(group)
        form.addRow(self.multithreaded)
        form.addRow("Max shrink iterations", self.max_shrink_iters)
        form.addRow("Ray intersector tolerance", self.intersector_tol)
        return group

    def _connect_signals(self) -> None:
        self.min_samples.valueChanged.connect(self._on_min_changed)
        self.max_samples.valueChanged.connect(self._on_max_changed)

    def _on_min_changed(self, value: int) -> None:
        self.max_samples.setMinimum(value + 1)

    def _on_max_changed(self, value: int) -> None:
        self.min_samples.setMaximum(value - 1)

    def load(self, params) -> None:
        self.min_samples.setValue(params.GetInt("SphereMinSamples", self.DEFAULT_MIN_SAMPLES))
        self.max_samples.setValue(params.GetInt("SphereMaxSamples", self.DEFAULT_MAX_SAMPLES))
        self.margin.setValue(params.GetFloat("SphereMargin", self.DEFAULT_MARGIN))
        self.multithreaded.setChecked(
            params.GetBool("SphereMultiThreaded", self.DEFAULT_MULTITHREADED)
        )
        self.max_shrink_iters.setValue(
            params.GetInt("SphereMaxShrinkIters", self.DEFAULT_MAX_SHRINK_ITERS)
        )
        self.intersector_tol.setValue(
            params.GetFloat("SphereIntersectorTol", self.DEFAULT_INTERSECTOR_TOL)
        )

    def save(self, params) -> None:
        params.SetInt("SphereMinSamples", self.min_samples.value())
        params.SetInt("SphereMaxSamples", self.max_samples.value())
        params.SetFloat("SphereMargin", self.margin.value())
        params.SetBool("SphereMultiThreaded", self.multithreaded.isChecked())
        params.SetInt("SphereMaxShrinkIters", self.max_shrink_iters.value())
        params.SetFloat("SphereIntersectorTol", self.intersector_tol.value())


class RayThicknessPanel(QtWidgets.QWidget):
    DEFAULT_MIN_SAMPLES = 5
    DEFAULT_MAX_SAMPLES = 10
    DEFAULT_MARGIN = 0.0
    DEFAULT_INTERSECTOR_TOL = 1e-3
    DEFAULT_NORMAL_CONE = 5.0
    DEFAULT_SEED_COVERAGE_THRESHOLD = 50

    def __init__(self):
        super().__init__()
        self._init_widgets()
        self._build_ui()
        self._connect_signals()

    def _init_widgets(self) -> None:
        self.min_samples: QtWidgets.QSpinBox = _pref(QtWidgets.QSpinBox(), "RayMinSamples")  # type: ignore
        self.min_samples.setRange(2, 99)

        self.max_samples: QtWidgets.QSpinBox = _pref(QtWidgets.QSpinBox(), "RayMaxSamples")  # type: ignore
        self.max_samples.setRange(3, 100)

        self.margin: QtWidgets.QDoubleSpinBox = _pref(QtWidgets.QDoubleSpinBox(), "RayMargin")  # type: ignore
        self.margin.setRange(0.0, 0.495)
        self.margin.setSingleStep(0.005)
        self.margin.setDecimals(3)
        self.margin.setToolTip("UV boundary margin. Prevents sampling too close to face edges.")

        self.intersector_tol: ToleranceSpinBox = _pref(ToleranceSpinBox(), "RayIntersectorTol")  # type: ignore
        self.intersector_tol.setToolTip(
            "Sets the load tolerance of the shape intersector. This affects how precisely rays detect surface intersections."
        )
        self.normal_cone: QtWidgets.QDoubleSpinBox = _pref(
            QtWidgets.QDoubleSpinBox(), "RayNormalCone"
        )  # type: ignore
        self.normal_cone.setSuffix("°")
        self.normal_cone.setRange(0.00, 30.00)
        self.normal_cone.setDecimals(2)
        self.normal_cone.setToolTip(
            "Controls how strict the face must be roughly perpendicular to the ray for it to be considered a valid thickness."
        )
        self.seed_coverage_threshold: QtWidgets.QSpinBox = _pref(
            QtWidgets.QSpinBox(), "RaySeedCoverageThreshold"
        )  # type: ignore
        self.seed_coverage_threshold.setRange(0, 100)
        self.seed_coverage_threshold.setSuffix("%")
        self.seed_coverage_threshold.setToolTip(
            "Controls the percentage of seeds required before the analyzer skips a face."
        )

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        title = QtWidgets.QLabel("Ray Thickness")
        font = title.font()
        font.setPointSize(16)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        layout.addWidget(self._build_sampling_group())
        layout.addWidget(self._build_algorithm_group())

        layout.addStretch()

    def _build_sampling_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Sampling Settings")
        form = QtWidgets.QFormLayout(group)
        form.addRow("Min sampling density", self.min_samples)
        form.addRow("Max sampling density", self.max_samples)
        form.addRow("Boundary margin", self.margin)
        return group

    def _build_algorithm_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Algorithm Settings")
        form = QtWidgets.QFormLayout(group)
        form.addRow("Ray intersector tolerance", self.intersector_tol)
        form.addRow("Secondary hit normal cone", self.normal_cone)
        form.addRow("Seed coverage threshold", self.seed_coverage_threshold)
        return group

    def _connect_signals(self) -> None:
        self.min_samples.valueChanged.connect(self._on_min_changed)
        self.max_samples.valueChanged.connect(self._on_max_changed)

    def _on_min_changed(self, value: int) -> None:
        self.max_samples.setMinimum(value + 1)

    def _on_max_changed(self, value: int) -> None:
        self.min_samples.setMaximum(value - 1)

    def load(self, params) -> None:
        self.min_samples.setValue(params.GetInt("RayMinSamples", self.DEFAULT_MIN_SAMPLES))
        self.max_samples.setValue(params.GetInt("RayMaxSamples", self.DEFAULT_MAX_SAMPLES))
        self.margin.setValue(params.GetFloat("RayMargin", self.DEFAULT_MARGIN))
        self.intersector_tol.setValue(
            params.GetFloat("RayIntersectorTol", self.DEFAULT_INTERSECTOR_TOL)
        )
        self.normal_cone.setValue(params.GetFloat("RayNormalCone", self.DEFAULT_NORMAL_CONE))
        self.seed_coverage_threshold.setValue(
            params.GetInt("RaySeedCoverageThreshold", self.DEFAULT_SEED_COVERAGE_THRESHOLD)
        )

    def save(self, params) -> None:
        params.SetInt("RayMinSamples", self.min_samples.value())
        params.SetInt("RayMaxSamples", self.max_samples.value())
        params.SetFloat("RayMargin", self.margin.value())
        params.SetFloat("RayIntersectorTol", self.intersector_tol.value())
        params.SetFloat("RayNormalCone", self.normal_cone.value())
        params.SetInt("RaySeedCoverageThreshold", self.seed_coverage_threshold.value())


class DFMPreferencesAnalyzers:
    def __init__(self):
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("Analyzers")
        self.form.setWindowIcon(QtGui.QIcon(":/icons/dfm_analysis.svg"))

        self.panels: list[_AnalyzerPanel] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout(self.form)

        self.list_widget = QtWidgets.QListWidget()
        self.stack = QtWidgets.QStackedWidget()

        self._register_panel("Sphere Thickness", SphereThicknessPanel())
        self._register_panel("Ray Thickness", RayThicknessPanel())

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(self.list_widget)
        splitter.addWidget(self.stack)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([150, 450])

        layout.addWidget(splitter)
        self.list_widget.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.list_widget.setCurrentRow(0)

    def _register_panel(self, label: str, panel: _AnalyzerPanel) -> None:
        self.list_widget.addItem(label)
        self.stack.addWidget(panel)  # type: ignore
        self.panels.append(panel)

    def loadSettings(self) -> None:
        params = App.ParamGet("User parameter:BaseApp/Preferences/Mod/DFM")
        for panel in self.panels:
            panel.load(params)

    def saveSettings(self) -> None:
        params = App.ParamGet("User parameter:BaseApp/Preferences/Mod/DFM")
        for panel in self.panels:
            panel.save(params)
