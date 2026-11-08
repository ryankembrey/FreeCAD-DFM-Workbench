# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The attributed adjacency graph and subgraph matching (internal)."""

from .aag import AAG, Arc, Blend  # noqa: F401
from .matching import Pattern, match  # noqa: F401
