# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The task panel shown while a DFM analysis runs.

The setup panel hands over to this one when Run is clicked, and this one hands
over to the results panel when the analysis is done (or back to a setup panel
with the same settings on Abort). It shows every rule of the process, so it
reads well with many rules and with rules measured at the same time on
several cores (see app/parallel):

* an overall bar, what is happening ("Measuring on 7 cores"), the time so far
  and the time left (app/run_estimate.py);
* a row per rule: queued, running (its own bar and how many cores are on it),
  checking, or done (its findings and time). The running rows are kept in
  view.

It runs the analysis itself (AnalysisRunner), reading its progress events.
"""

import time
from typing import Any, Optional

from PySide6 import QtCore, QtGui, QtWidgets

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ..app.analysis_runner import AnalysisRunner
from ..app.run_estimate import RunEstimate, ShownEstimate, describe, load_history, save_history
from .utils import resources  # noqa: F401
from .utils.direction_row import muted_color

_DONE = "#3a9a4a"
_FOUND = "#d08a1e"
_RUNNING = "#2f7fd0"
_UI_INTERVAL = 0.08  # s between redraws while faces stream in


class RunRequest:
    """Everything the run needs, taken from the setup panel."""

    def __init__(self, target_object, shape, process, process_name, material_name, kwargs,
                 setup_state):
        self.target_object = target_object
        self.shape = shape
        self.process = process
        self.process_name = process_name
        self.material_name = material_name
        self.kwargs = kwargs
        self.setup_state = setup_state


def _clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(seconds, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    return _clock(seconds)


class _ElidedLabel(QtWidgets.QLabel):
    """A label that ends in "…" when it is too narrow, instead of being cut."""

    def __init__(self, text: str):
        super().__init__(text)
        self._full = text
        self.setToolTip(text)
        self.setMinimumWidth(40)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                           QtWidgets.QSizePolicy.Policy.Preferred)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        text = self.fontMetrics().elidedText(self._full, QtCore.Qt.TextElideMode.ElideRight,
                                             self.width())
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.rect(), int(self.alignment() | QtCore.Qt.AlignmentFlag.AlignVCenter),
                         text)


class _RuleRow:
    """One rule's line: state mark, name, and either a bar with its share
    (while running) or a line of details (queued, checking, done)."""

    def __init__(self, grid: QtWidgets.QGridLayout, row: int, rule, analyzer_id: str):
        self.rule = rule
        self.analyzer_id = analyzer_id
        self.mark = QtWidgets.QLabel()
        self.mark.setFixedWidth(16)
        self.mark.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.name = _ElidedLabel(getattr(rule, "label", None)
                                 or rule.name.replace("_", " ").capitalize())

        # Bar and details share the right-hand column.
        self.cell = QtWidgets.QWidget()
        cell = QtWidgets.QHBoxLayout(self.cell)
        cell.setContentsMargins(0, 0, 0, 0)
        cell.setSpacing(6)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.setFixedWidth(64)
        self.bar.setRange(0, 1000)
        self.detail = QtWidgets.QLabel()
        self.detail.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight
                                 | QtCore.Qt.AlignmentFlag.AlignVCenter)
        cell.addStretch(1)
        cell.addWidget(self.bar)
        cell.addWidget(self.detail)
        grid.addWidget(self.mark, row, 0)
        grid.addWidget(self.name, row, 1)
        grid.addWidget(self.cell, row, 2)
        self.state = None

    def show(self, state: str, mark: str, color: Optional[str], detail: str,
             fraction: Optional[float] = None, muted: str = "") -> None:
        self.state = state
        self.mark.setText(mark)
        self.mark.setStyleSheet(f"color: {color};" if color else f"color: {muted};")
        quiet = state == "queued"
        self.name.setStyleSheet(f"color: {muted};" if quiet else "")
        self.detail.setText(detail)
        self.detail.setStyleSheet(f"color: {muted};")
        if fraction is None:
            self.bar.setVisible(False)
        else:
            self.bar.setVisible(True)
            self.bar.setValue(int(max(0.0, min(1.0, fraction)) * 1000))


class TaskProgress:
    def __init__(self, request: RunRequest):
        self.request = request
        self.running = False
        self.abort_requested = False
        self.form = self._build_form()

        self._rows: list[_RuleRow] = []
        self._plan_steps = 1
        self._step = 0
        self._serial: dict[str, dict] = {}  # analyzers measured in FreeCAD's process
        self._parallel: dict[str, dict] = {}  # analyzers measured by the workers
        self._workers = 0
        self._checks: dict[Any, tuple] = {}  # rule -> (findings, seconds)
        self._phase = "Getting started"
        self._started = time.monotonic()
        self._estimate: Optional[RunEstimate] = None
        self._shown = ShownEstimate()
        self._bar = 0.0  # the overall bar never goes back
        self._last_ui = 0.0
        self._followed = None

        self._clock_timer = QtCore.QTimer(self.form)
        self._clock_timer.setInterval(500)
        self._clock_timer.timeout.connect(self._refresh_time)
        QtCore.QTimer.singleShot(60, self._run)

    # -- Building -------------------------------------------------------------

    def _build_form(self) -> QtWidgets.QWidget:
        form = QtWidgets.QWidget()
        form.setWindowTitle("Running DFM Analysis")
        form.setWindowIcon(QtGui.QIcon(":/icons/dfm_analysis.svg"))
        root = QtWidgets.QVBoxLayout(form)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(8)

        req = self.request
        title = QtWidgets.QLabel(getattr(req.target_object, "Label", "") or "Model")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        root.addWidget(title)
        sub = QtWidgets.QLabel(f"{req.process_name} · {req.material_name}")
        sub.setStyleSheet(f"color: {muted_color(sub)};")
        root.addWidget(sub)

        box = QtWidgets.QGroupBox("Progress")
        lay = QtWidgets.QVBoxLayout(box)
        self.overall = QtWidgets.QProgressBar()
        self.overall.setRange(0, 1000)
        self.overall.setTextVisible(True)
        self.overall.setFormat("%p%")
        lay.addWidget(self.overall)
        line = QtWidgets.QHBoxLayout()
        self.phase_label = QtWidgets.QLabel("Getting started")
        self.time_label = QtWidgets.QLabel("0:00")
        self.time_label.setStyleSheet(f"color: {muted_color(self.time_label)};")
        line.addWidget(self.phase_label, 1)
        line.addWidget(self.time_label)
        lay.addLayout(line)
        root.addWidget(box)

        rules = QtWidgets.QGroupBox("Rules")
        rules_lay = QtWidgets.QVBoxLayout(rules)
        self.summary = QtWidgets.QLabel("")
        self.summary.setStyleSheet(f"color: {muted_color(self.summary)};")
        rules_lay.addWidget(self.summary)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        holder = QtWidgets.QWidget()
        self.grid = QtWidgets.QGridLayout(holder)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(4)
        self.grid.setColumnStretch(1, 1)
        self.scroll.setWidget(holder)
        self.scroll.setMinimumHeight(120)
        rules_lay.addWidget(self.scroll, 1)
        root.addWidget(rules, 1)
        self._muted = muted_color(self.summary)
        return form

    def _build_rows(self, rules: list) -> None:
        for i, entry in enumerate(rules):
            self._rows.append(_RuleRow(self.grid, i, entry["rule"], entry["analyzer"]))
        self.grid.setRowStretch(len(rules), 1)
        # Room for up to ~12 rules before it scrolls.
        self.scroll.setMinimumHeight(min(12, max(3, len(rules))) * 24)

    # -- Running ----------------------------------------------------------------

    def _run(self) -> None:
        req = self.request
        self.running = True
        self._started = time.monotonic()
        self._clock_timer.start()

        def progress_cb(current: int, total: int, message: str = ""):
            self._step, self._plan_steps = current, max(total, 1)
            self._maybe_refresh()
            QtWidgets.QApplication.processEvents(
                QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                | QtCore.QEventLoop.ProcessEventsFlag.WaitForMoreEvents,
                5,
            )

        def check_abort() -> bool:
            QtWidgets.QApplication.processEvents()
            return self.abort_requested

        runner = AnalysisRunner()
        results, failed = None, False
        try:
            results = runner.run_analysis(
                process_name=req.process_name,
                material_name=req.material_name,
                shape=req.shape,
                progress_cb=progress_cb,
                check_abort=check_abort,
                events=self._on_event,
                **dict(req.kwargs),
            )
        except Exception as exc:
            import traceback

            App.Console.PrintError(f"A critical error occurred during analysis: {exc}\n")
            App.Console.PrintError(traceback.format_exc())
            failed = True
        finally:
            self.running = False
            try:
                self._clock_timer.stop()
            except RuntimeError:
                pass

        if self.abort_requested or failed or results is None:
            if self.abort_requested:
                App.Console.PrintMessage("DFM Analysis aborted by user.\n")
            self._back_to_setup()
            return
        if self._estimate is not None:
            save_history(self._estimate.learned())  # a better first guess next time
        try:
            self.overall.setValue(1000)
            self.time_label.setText(_clock(time.monotonic() - self._started))
        except RuntimeError:
            pass
        self._show_phase("Preparing the results")
        from .task_setup import open_results

        Gui.Control.closeDialog()
        open_results(req.target_object, req.process, req.process_name, req.material_name,
                     results, runner, req.kwargs)

    def _back_to_setup(self) -> None:
        from .task_setup import TaskSetup

        Gui.Control.closeDialog()
        setup = TaskSetup()
        try:
            setup.restore(self.request.setup_state)
        except Exception as exc:
            App.Console.PrintWarning(f"DFM: could not restore the analysis settings. {exc}\n")
        Gui.Control.showDialog(setup)

    # -- Events from the runner ----------------------------------------------------

    def _on_event(self, kind: str, data: dict) -> None:
        if self._estimate is not None:
            self._estimate.on_event(kind, data)
        if kind == "plan":
            self._plan_steps = max(1, data.get("total_steps", 1))
            if not self._rows:
                self._build_rows(data.get("rules", []))
            self._estimate = RunEstimate(data.get("rules", []), load_history())
            self._refresh(force=True)
        elif kind == "workers":
            self._workers = data.get("workers", 0)
            self._parallel = data.get("analyzers", {})
            self._phase = f"Measuring on {self._workers} cores" if self._workers > 1 else "Measuring"
            self._maybe_refresh()
        elif kind == "analyzer":
            self._serial[data["id"]] = dict(data)
            self._phase = "Measuring"
            self._maybe_refresh(force=data.get("state") == "done" or data.get("done") == 0)
        elif kind == "check":
            self._checks[data["rule"]] = (data.get("findings", 0), data.get("seconds", 0.0))
            self._phase = "Checking the results"
            self._maybe_refresh(force=True)

    # -- Drawing ---------------------------------------------------------------------

    def _maybe_refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_ui >= _UI_INTERVAL:
            self._last_ui = now
            self._refresh()

    def _show_phase(self, text: str) -> None:
        try:
            self.phase_label.setText(text)
            QtWidgets.QApplication.processEvents()
        except RuntimeError:
            pass

    def _refresh(self, force: bool = False) -> None:
        try:
            self._draw()
        except RuntimeError:
            # The panel was closed under us (another command took the task
            # view): stop the analysis rather than draw into deleted widgets.
            self.abort_requested = True

    def _analyzer_view(self, aid: str) -> tuple:
        """(state, fraction, cores, seconds) of an analyzer."""
        now = time.monotonic()
        p = self._parallel.get(aid)
        if p is not None:
            total = max(p.get("total") or 1, 1)
            fraction = p.get("done", 0) / total
            started, finished = p.get("started"), p.get("finished")
            if finished is not None:
                return "measured", 1.0, 0, finished - (started or finished)
            if started is not None:
                return "running", fraction, p.get("busy", 0), now - started
            return "queued", 0.0, 0, 0.0
        s = self._serial.get(aid)
        if s is not None:
            total = max(s.get("total") or 1, 1)
            if s.get("state") == "done":
                return "measured", 1.0, 0, s.get("seconds", 0.0)
            return "running", s.get("done", 0) / total, 1, s.get("seconds", 0.0)
        return "queued", 0.0, 0, 0.0

    def _draw(self) -> None:
        if self.abort_requested:
            self.phase_label.setText("Stopping…")
        else:
            self.phase_label.setText(self._phase)

        done = 0
        first_running = None
        for row in self._rows:
            checked = self._checks.get(row.rule)
            state, fraction, cores, seconds = self._analyzer_view(row.analyzer_id)
            if checked is not None:
                done += 1
                findings, _check_seconds = checked
                if findings:
                    text = f"{findings} finding{'s' if findings != 1 else ''}"
                    row.show("done", "▲", _FOUND, f"{text} · {_duration(seconds)}",
                             muted=self._muted)
                else:
                    row.show("done", "✓", _DONE, f"No issues · {_duration(seconds)}",
                             muted=self._muted)
            elif state == "measured":
                row.show("checking", "◐", _RUNNING, "Checking", muted=self._muted)
            elif state == "running":
                if first_running is None:
                    first_running = row
                if self._parallel.get(row.analyzer_id) is not None:
                    detail = (f"{cores} core{'s' if cores != 1 else ''} · {fraction:.0%}"
                              if cores else f"Waiting · {fraction:.0%}")
                else:
                    detail = f"{fraction:.0%}"
                row.show("running", "●", _RUNNING, detail, fraction, muted=self._muted)
            else:
                row.show("queued", "○", None, "Queued", muted=self._muted)
        if self._rows:
            self.summary.setText(f"{done} of {len(self._rows)} rules checked")
        if first_running is not None and first_running is not self._followed:
            self._followed = first_running
            self.scroll.ensureWidgetVisible(first_running.name, 0, 24)
        self._refresh_time()

    def _refresh_time(self) -> None:
        """The time so far and left, and the overall bar: the share of the
        time (so it agrees with the time left), else of the faces measured."""
        try:
            now = time.monotonic()
            elapsed = now - self._started
            text = _clock(elapsed)
            fraction = self._step / max(self._plan_steps, 1)
            if self.running and self._estimate is not None:
                left = self._shown.update(self._estimate.remaining(), now)
                text += " · " + (describe(left) if left is not None
                                 else "working out the time left")
                if left is not None:
                    fraction = elapsed / max(elapsed + left, 1e-6)
            self._bar = max(self._bar, min(0.99 if self.running else 1.0, fraction))
            self.overall.setValue(int(1000 * self._bar))
            self.time_label.setText(text)
        except RuntimeError:
            pass

    # -- Task panel ----------------------------------------------------------------------

    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.StandardButton.Abort

    def reject(self):
        """Abort: stop the analysis; it then goes back to the settings."""
        if self.running:
            self.abort_requested = True
            self._refresh(force=True)
            return False  # stay open until the analysis has stopped
        return True

    def accept(self):
        return self.reject()
