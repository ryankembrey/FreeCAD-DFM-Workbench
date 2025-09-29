# This is a temporary file made for the purpose of testing the analyzers and checkers

from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.gp import gp_Dir

import os


from analyzers import DraftAnalyzer
from checks import DraftAngleCheck


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


def run_draft():
    step_file = "/home/Ryan/documents/git/FreeCAD-DFM-Workbench/draft.step"
    shape: TopoDS_Shape = import_step(step_file)
    analyzer_params = {"pull_direction": gp_Dir(0, 0, 1), "samples": 50}

    draft_analyzer = DraftAnalyzer()
    data = draft_analyzer.execute(shape, **analyzer_params)
    params = {"min_angle": 4.0}
    dac = DraftAngleCheck()
    dac.run_check(analysis_data_map=data, parameters=params, check_type="MIN_DRAFT_ANGLE")
