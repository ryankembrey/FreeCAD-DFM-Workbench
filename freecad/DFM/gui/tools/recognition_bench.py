# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""DFM_RecognitionBench: the feature recognition test bench (developer tool).

Available only when Preferences > DFM > General > Developer Options >
"Feature recognition test bench" is on: the command is always registered,
but greyed out while the option is off, and the workbench only adds it to
its DFM Developer toolbar and menu when the option is on.
"""

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from ...app.recognition_service import bench_enabled

_ICON = ":/icons/dfm_analysis.svg"


class RecognitionBenchCommand:
    def GetResources(self):
        return {
            "Pixmap": _ICON,
            "MenuText": "Feature Recognition Bench",
            "ToolTip": "Developer tool: inspect, test and approve the feature recognition engine.",
        }

    def Activated(self):
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        from ..recognition_bench.panel import RecognitionBenchPanel
        Gui.Control.showDialog(RecognitionBenchPanel())

    def IsActive(self):
        return bench_enabled()


if App.GuiUp:
    Gui.addCommand("DFM_RecognitionBench", RecognitionBenchCommand())
