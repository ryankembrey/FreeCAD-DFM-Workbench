# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Hooks the feature recognition package's own tests into run_tests.py.

The package keeps its tests next to its code
(freecad/DFM/core/recognition/tests) so it can be developed and tested like
a library on its own; this file only collects them.
"""

import os
import unittest

import freecad.DFM.core.recognition.tests as _pkg_tests


def load_tests(loader: unittest.TestLoader, standard_tests, pattern):
    here = os.path.dirname(os.path.abspath(_pkg_tests.__file__))
    top = os.path.abspath(os.path.join(here, "..", "..", "..", "..", ".."))
    standard_tests.addTests(loader.discover(start_dir=here, pattern="test_*.py", top_level_dir=top))
    return standard_tests
