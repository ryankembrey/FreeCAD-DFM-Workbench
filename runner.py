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

from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.gp import gp_Dir

from Analyzers.DraftAnalyzer import DraftAnalyzer


def import_step(model_path: str) -> TopoDS_Shape:
    print(f"Reading STEP file from: {model_path}")

    step_reader = STEPControl_Reader()
    status = step_reader.ReadFile(model_path)

    if status != IFSelect_RetDone:
        raise FileNotFoundError(f"Could not read the STEP file at {model_path}")

    step_reader.TransferRoots()
    return step_reader.OneShape()


def _run_analyzer_test():
    print("DFM WB running startup test…")

    try:
        model_path = "/home/Ryan/documents/git/FreeCAD-DFM-Workbench/tapered_pad.step"

        test_shape = import_step(model_path)

        draft_analyzer = DraftAnalyzer()

        pull_dir = gp_Dir(0, 0, 1)
        print(f"{draft_analyzer.name} using pull direction: Z+")

        analysis_results = draft_analyzer.execute(
            shape=test_shape, pull_direction=pull_dir
        )

        print("\nTest results:")
        if not analysis_results:
            print("The analysis returned no results.")
        else:
            face_index = 1
            for face, angle in analysis_results.items():
                print(f"Face #{face_index}: Draft Angle = {angle:.2f} degrees")
                face_index += 1

    except Exception as e:
        print(f"\nDFM TEST FAILED: An error occurred -> {e}")
        import traceback

        traceback.print_exc()


def main():
    _run_analyzer_test()
