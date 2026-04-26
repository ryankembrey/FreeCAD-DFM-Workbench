# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from ...core.models import Severity


def severity_color(severity: Severity) -> str:
    return {
        Severity.ERROR: "#E24B4A",
        Severity.WARNING: "#D4900A",
        Severity.INFO: "#378ADD",
        Severity.SUCCESS: "#639922",
    }.get(severity, "#639922")
