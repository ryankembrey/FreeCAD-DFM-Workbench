# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Visual rule information system.

Provides illustrated popup panels explaining what each DFM rule measures.
Designed to be invoked from any part of the UI — process library cards,
results panels, or anywhere a Rulebook reference is available.
"""

from ._panel import RuleInfoPanel

__all__ = ["RuleInfoPanel"]
