# This is a temporary file made for the purpose of testing the analyzers and checkers

import FreeCAD
import FreeCADGui as Gui
import Part

from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.gp import gp_Dir

import os


from analyzers import DraftAnalyzer, ThicknessAnalyzer
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


def run_draft(subject: Part.Shape):
    shape_to_analyze: TopoDS_Shape = Part.__toPythonOCC__(subject)
    analyzer_params = {"pull_direction": gp_Dir(0, 0, 1), "samples": 4}

    draft_analyzer = DraftAnalyzer()
    data = draft_analyzer.execute(shape_to_analyze, **analyzer_params)
    params = {"min_angle": 3.0}
    dac = DraftAngleCheck()
    faces = dac.run_check(analysis_data_map=data, parameters=params, check_type="MIN_DRAFT_ANGLE")
    return faces


def run_thickness(subject: Part.Shape):
    shape_to_analyze: TopoDS_Shape = Part.__toPythonOCC__(subject)
    analyzer_params = {"method": "ray-cast"}

    thickness_analyzer = ThicknessAnalyzer()
    data = thickness_analyzer.execute(shape_to_analyze, **analyzer_params)
    FreeCAD.Console.PrintMessage(f"{data}")

    faces = []
    for face in data:
        faces.append(face)
    return faces
