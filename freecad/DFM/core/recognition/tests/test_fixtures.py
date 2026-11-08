# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Every fixture part, in every variant (as built, moved, all-B-spline, STEP
round trip), as a unittest sub-test each. The same cases the GUI bench runs.

Set DFM_RECOGNITION_VARIANTS=base to run only the parts as built (quicker).
"""

import os
import unittest

from freecad.DFM.core.recognition.testing import fixtures as FX
from freecad.DFM.core.recognition.testing.run import VARIANTS, run_case

_VARIANTS = tuple(v for v in os.environ.get("DFM_RECOGNITION_VARIANTS", ",".join(VARIANTS)).split(",") if v)


def _make(case):
    def test(self):
        base = case.build()
        for variant in _VARIANTS:
            if variant not in case.variants:
                continue
            with self.subTest(variant=variant):
                report, _shape, _result = run_case(case, variant, base_shape=base)
                self.assertTrue(report.passed, "\n" + "\n".join(report.failures))
    test.__doc__ = f"{case.name}: {case.notes or ', '.join(case.tags)}"
    return test


class TestFixtures(unittest.TestCase):
    pass


for _case in FX.CASES:
    setattr(TestFixtures, f"test_{_case.name}", _make(_case))


if __name__ == "__main__":
    unittest.main()
