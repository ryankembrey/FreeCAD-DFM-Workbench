# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Imports every check module so its @register_check decorator runs."""

import importlib
import pkgutil


def _import_submodules() -> None:
    for _, name, _ in pkgutil.iter_modules(__path__):
        if not name.startswith("_"):
            importlib.import_module(f".{name}", __name__)


_import_submodules()
