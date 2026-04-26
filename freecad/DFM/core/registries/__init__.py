# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

from .analyzers_registry import register_analyzer, get_analyzer_class
from .checks_registry import register_check, get_check_class
from .process_registry import ProcessRegistry
