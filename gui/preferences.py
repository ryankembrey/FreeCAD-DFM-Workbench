from typing import Protocol

from PySide6 import QtGui, QtWidgets, QtCore

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from gui.widgets import ToleranceSpinBox

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
        grid = QtWidgets.QGridLayout(group)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.addWidget(self.print_timing_report, 0, 0)

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
        layout.addWidget(self._build_group())
        layout.addStretch()

    def _build_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Sphere Thickness Analyzer")
        grid = QtWidgets.QGridLayout(group)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        grid.addWidget(QtWidgets.QLabel("Min sampling density"), 0, 0)
        grid.addWidget(self.min_samples, 0, 1)
        grid.addWidget(QtWidgets.QLabel("Max sampling density"), 1, 0)
        grid.addWidget(self.max_samples, 1, 1)
        grid.addWidget(QtWidgets.QLabel("Boundary margin"), 2, 0)
        grid.addWidget(self.margin, 2, 1)
        grid.addWidget(self.multithreaded, 3, 0, 1, 2)
        grid.addWidget(QtWidgets.QLabel("Max shrink iterations"), 4, 0)
        grid.addWidget(self.max_shrink_iters, 4, 1)
        grid.addWidget(QtWidgets.QLabel("Ray intersector tolerance"), 5, 0)
        grid.addWidget(self.intersector_tol, 5, 1)

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
