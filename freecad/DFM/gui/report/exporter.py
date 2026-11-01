# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Write a PDF report: capture the viewport images, then write the pages."""

import os
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

import FreeCAD as App  # type: ignore

from .data import ImageJob, ReportData, image_jobs
from .layout import ReportLayout, write_pdf
from .settings import ReportSettings
from .template import ReportTemplate

_MAX_IMAGE_PX = 3000


def image_size(job: ImageJob, settings: ReportSettings, layout: ReportLayout) -> tuple[int, int]:
    """Pixel size to capture *job* at, from its width on the page and the resolution."""
    width_pt = layout.image_width(job.key)
    width = min(round(width_pt / 72.0 * settings.image_dpi), _MAX_IMAGE_PX)
    return width, max(1, round(width / job.aspect))


def estimate_bytes(settings: ReportSettings, layout: ReportLayout, jobs: list[ImageJob],
                   pages: int) -> int:
    """A rough PDF size: renders of a part compress to about 0.35 bytes a pixel."""
    total = 40_000 + 12_000 * pages
    for job in jobs:
        w, h = image_size(job, settings, layout)
        total += int(w * h * 0.35)
    return total


def format_bytes(n: int) -> str:
    if n < 1_000_000:
        return f"{max(n // 1000, 1)} kB"
    return f"{n / 1_000_000:.1f} MB".replace(".0 MB", " MB")


def export_pdf(parent: Optional[QtWidgets.QWidget], path: str, data: ReportData,
               settings: ReportSettings, template: ReportTemplate, capture,
               picks: Optional[set[int]] = None) -> bool:
    """Capture images with progress, write the PDF to *path* and open it.

    *capture* is a ``ViewportCapture`` (or anything with ``begin()``,
    ``capture(job, w, h)`` and ``end()``); None writes placeholders.
    Returns True when the file was written.
    """
    layout = ReportLayout(data, settings, template, picks)
    pages = layout.build()
    jobs = image_jobs(data, settings, picks)
    if capture is None or not getattr(capture, "available", True):
        jobs_to_capture = []
    else:
        jobs_to_capture = jobs

    steps = len(jobs_to_capture) + len(pages)
    progress = QtWidgets.QProgressDialog("Preparing the report…", "Cancel", 0, steps, parent)
    progress.setWindowTitle("Export PDF report")
    progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    progress.setMinimumWidth(340)
    progress.setValue(0)
    QtWidgets.QApplication.processEvents()

    images: dict[str, QtGui.QImage] = {}
    try:
        if jobs_to_capture:
            capture.begin()
            try:
                for i, job in enumerate(jobs_to_capture):
                    if progress.wasCanceled():
                        return False
                    progress.setLabelText(f"Capturing viewport image {i + 1} of {len(jobs_to_capture)}…")
                    progress.setValue(i)
                    QtWidgets.QApplication.processEvents()
                    w, h = image_size(job, settings, layout)
                    try:
                        image = capture.capture(job, w, h)
                    except Exception as exc:  # keep going; the page shows a placeholder
                        App.Console.PrintWarning(f"DFM: could not capture a report image: {exc}\n")
                        image = None
                    if image is not None:
                        images[job.key] = image
            finally:
                capture.end()
        if progress.wasCanceled():
            return False

        progress.setLabelText("Writing the PDF…")
        offset = len(jobs_to_capture)

        def step(n: int) -> bool:
            progress.setValue(offset + n)
            QtWidgets.QApplication.processEvents()
            return not progress.wasCanceled()

        try:
            ok = write_pdf(path, pages, template, layout.ts, images, title=data.title(settings),
                           progress=step)
        except Exception as exc:
            App.Console.PrintError(f"DFM: writing the PDF report failed: {exc}\n")
            ok = False
        if not ok:
            _remove(path)
            if not progress.wasCanceled():
                QtWidgets.QMessageBox.warning(
                    parent, "Export PDF report",
                    f"The report could not be written to\n{path}\n\n"
                    "Check that the folder exists and the file isn't open in another program.")
            return False
    finally:
        progress.close()

    App.Console.PrintMessage(f"DFM: PDF report saved to {path}\n")
    if settings.open_after:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
    return True


def _remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def ask_path(parent: Optional[QtWidgets.QWidget], data: ReportData,
             settings: ReportSettings) -> Optional[str]:
    """Ask where to save the report, suggesting a name from the file name pattern."""
    from ...app.prefs import dfm_params

    folder = dfm_params().GetString("ReportLastFolder", "") or os.path.expanduser("~")
    suggested = os.path.join(folder, data.file_name(settings))
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        parent, "Export PDF report", suggested, "PDF files (*.pdf)")
    if not path:
        return None
    if not path.lower().endswith(".pdf"):
        path += ".pdf"
    dfm_params().SetString("ReportLastFolder", os.path.dirname(path))
    return path
