#  ***************************************************************************
#  *   Copyright (c) 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>              *
#  *                                                                         *
#  *   This file is part of the FreeCAD CAx development system.              *
#  *                                                                         *
#  *   This library is free software; you can redistribute it and/or         *
#  *   modify it under the terms of the GNU Library General Public           *
#  *   License as published by the Free Software Foundation; either          *
#  *   version 2 of the License, or (at your option) any later version.      *
#  *                                                                         *
#  *   This library  is distributed in the hope that it will be useful,      *
#  *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
#  *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
#  *   GNU Library General Public License for more details.                  *
#  *                                                                         *
#  *   You should have received a copy of the GNU Library General Public     *
#  *   License along with this library; see the file COPYING.LIB. If not,    *
#  *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
#  *   Suite 330, Boston, MA  02111-1307, USA                                *
#  *                                                                         *
#  ***************************************************************************

import sys
import os
import traceback

project_root = os.path.dirname(__file__)
src_path = os.path.join(project_root, "src")
if src_path not in sys.path:
    sys.path.append(src_path)

from analysis_runner import AnalysisRunner
from enums import CheckType
from data_types import CheckResult

from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.gp import gp_Dir


def import_step(model_path: str) -> TopoDS_Shape:
    """Loads a shape from a STEP file. Raises exceptions on failure."""
    print(f"Reading STEP file from: {os.path.basename(model_path)}")
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Could not read the STEP file at {model_path}")

    step_reader = STEPControl_Reader()
    status = step_reader.ReadFile(model_path)
    if status != IFSelect_RetDone:
        raise IOError(f"STEP reader failed to process file: {model_path}")

    step_reader.TransferRoots()
    shape = step_reader.OneShape()
    if shape.IsNull():
        raise ValueError("STEP file did not contain a valid shape.")
    return shape


def main():
    """
    Main entry point for the test runner.
    """
    print("--- DFM Workbench Test Runner ---")
    try:
        model_path = os.path.join(project_root, "dfm_test.step")

        analyzer_params = {"pull_direction": gp_Dir(0, 0, 1), "samples": 50}

        user_selected_checks = {
            CheckType.MIN_DRAFT_ANGLE: {"min_angle": 2},
        }

        test_shape = import_step(model_path)

        runner = AnalysisRunner()

        print("\n--- Starting DFM Analysis ---")
        dfm_findings = runner.run(test_shape, user_selected_checks, analyzer_params)
        print("--- Analysis Complete ---")

        print("\n--- FINAL DFM FINDINGS ---")
        if not dfm_findings:
            print("No DFM violations found. Good job!")
        else:
            dfm_findings.sort(key=lambda f: f.severity.value, reverse=True)
            for finding in dfm_findings:
                print(f"[{finding.severity.name}] {finding.check_name}: {finding.message}")
        print("--------------------------\n")

    except Exception as e:
        print(f"\nDFM TEST FAILED: An error occurred -> {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
