# This file is for the purposes of running the analysis from the Gui, so that results can be highlighted for debugging

import FreeCAD
import FreeCADGui as Gui
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face
import Part

from runner import run_draft


class DFM_Runner:
    def __init__(self):
        FreeCAD.Console.PrintMessage("Running DFM analysis from the GUI\n")

        try:
            doc_object = Gui.Selection.getSelection()[0]
            shape_to_analyze = doc_object.Shape
            FreeCAD.Console.PrintMessage(f"Subject: {doc_object.Label}\n")
        except IndexError:
            FreeCAD.Console.PrintUserError("ERROR: Select an object in the Tree view first.\n")
            return
        except Exception as e:
            FreeCAD.Console.PrintError(f"ERROR: Could not get a valid shape to analyze. {e}\n")
            return

        faces: list[TopoDS_Face] = run_draft(shape_to_analyze)

        Gui.Selection.clearSelection()
        self.highlight_faces(doc_object, faces)

    def highlight_faces(self, doc_object, failing_topo_faces: list[TopoDS_Face]):
        """Highlights the given faces on the specified document object."""

        if not failing_topo_faces:
            FreeCAD.Console.PrintMessage("No failing faces found. Model passes the check.\n")
            return

        FreeCAD.Console.PrintMessage(
            f"Found {len(failing_topo_faces)} failing faces. Highlighting them now.\n"
        )

        shape_faces = doc_object.Shape.Faces
        failing_face_names = []

        for failing_face_occ in failing_topo_faces:
            for i, part_face in enumerate(shape_faces):
                part_face_occ = Part.__toPythonOCC__(part_face)

                if part_face_occ.IsSame(failing_face_occ):
                    face_name = f"Face{i + 1}"
                    failing_face_names.append(face_name)
                    break

        if failing_face_names:
            FreeCAD.Console.PrintMessage(f"Highlighting: {', '.join(failing_face_names)}\n")
            Gui.Selection.addSelection(doc_object, failing_face_names)


class DfmAnalysisCommand:
    def GetResources(self):
        return {
            "Pixmap": "",
            "MenuText": "Run DFM Analysis",
            "ToolTip": "Opens the DFM Analysis task panel.",
        }

    def Activated(self):
        dfm_runner = DFM_Runner()

    def IsActive(self):
        return True


if FreeCAD.GuiUp:
    Gui.addCommand("DFM_RunAnalysis", DfmAnalysisCommand())
