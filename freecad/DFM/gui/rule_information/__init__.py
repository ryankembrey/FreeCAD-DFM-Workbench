# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Visual rule information system.

Explains what each DFM rule measures with a bad / good diagram and the
rule's target and limit values. Usable anywhere a Rulebook reference is
available.

Public API
----------
``RuleDiagramView``
    Embeddable widget: brief, diagram and target / limit legend. Used by the
    rule editing dialog in the process library.
``RuleInfoPanel``
    Popup wrapping a ``RuleDiagramView`` with the rule title, shown next to
    an anchor widget with ``show_near(anchor)``. Intended for the results
    panel.
"""

from ._panel import RuleInfoPanel
from ._view import RuleDiagramView

__all__ = ["RuleDiagramView", "RuleInfoPanel"]
