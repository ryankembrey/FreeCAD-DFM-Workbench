# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import FreeCAD
import FreeCADGui as Gui
import Part

from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCC.Core.BRep import BRep_Tool
from OCC.Core.GeomLProp import GeomLProp_SLProps
from OCC.Core.TopoDS import topods
from ..dfm.utils.geometry import get_face_uv_center


class TaskShowNormals:
    def __init__(self):
        self.debug_show_normals(Gui.Selection.getSelection()[0], length=10.0)

    def debug_show_normals(self, doc_object, length=5.0):
        """
        Creates an object in the tree showing the
        calculated normal for every face on the target object.
        """
        shape = Part.__toPythonOCC__(doc_object.Shape)
        explorer = TopExp_Explorer(shape, TopAbs_FACE)  # type: ignore

        debug_lines = []

        while explorer.More():
            face = topods.Face(explorer.Current())

            u, v = get_face_uv_center(face)

            surface = BRep_Tool.Surface(face)
            props = GeomLProp_SLProps(surface, u, v, 1, 1e-6)

            if props.IsNormalDefined():
                pnt = props.Value()
                norm = props.Normal()

                if not face.Location().IsIdentity():
                    pnt.Transform(face.Location().Transformation())
                    norm.Transform(face.Location().Transformation())

                if face.Orientation() == TopAbs_REVERSED:
                    norm.Reverse()

                v1 = FreeCAD.Vector(pnt.X(), pnt.Y(), pnt.Z())
                v2 = FreeCAD.Vector(
                    pnt.X() + norm.X() * length,
                    pnt.Y() + norm.Y() * length,
                    pnt.Z() + norm.Z() * length,
                )

                line = Part.makeLine(v1, v2)
                debug_lines.append(line)

            explorer.Next()

        if debug_lines:
            comp = Part.makeCompound(debug_lines)
            obj = FreeCAD.ActiveDocument.addObject("Part::Feature", "Debug_Normals")
            obj.Shape = comp
            obj.ViewObject.ShapeColor = (1.0, 0.0, 0.0)
            obj.ViewObject.LineWidth = 2.0
            FreeCAD.ActiveDocument.recompute()
            print(f"Created debug object with {len(debug_lines)} normals.")
        else:
            print("No faces found.")


class DfmAnalysisCommand:
    def GetResources(self):
        return {
            "Pixmap": "",
            "MenuText": "Show Normals",
            "ToolTip": "Displays lines overlaid over the selected shape, indicating the normal direction of each face.",
        }

    def Activated(self):
        TaskShowNormals()

    def IsActive(self):
        return True


if FreeCAD.GuiUp:
    Gui.addCommand("DFM_ShowNormals", DfmAnalysisCommand())
