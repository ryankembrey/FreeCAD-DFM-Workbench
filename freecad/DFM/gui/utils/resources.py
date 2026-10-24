# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Importing this module registers the compiled Qt resources (``:/icons/...``).

from .utils import resources  # noqa: F401
"""

try:
    from .. import DFM_rc  # noqa: F401
except Exception:
    pass
