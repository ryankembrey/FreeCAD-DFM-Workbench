# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""A minimal look-alike of FreeCAD's Part module over OCP: just what
app/recognition_hints.py uses (Faces, tessellate, Surface, distToShape,
Compound, Vertex), so hint extraction runs on real geometry."""
import types, sys
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRep import BRep_Tool, BRep_Builder
from OCP.TopLoc import TopLoc_Location
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.TopoDS import TopoDS_Compound
from OCP.gp import gp_Pnt
from freecad.DFM.core.recognition.testing import shapes as B
import freecad_stubs as stubs
V = stubs.app.Vector
def _vadd(a, b): return V(a.x+b.x, a.y+b.y, a.z+b.z)
def _vdiv(a, s): return V(a.x/s, a.y/s, a.z/s)
V.__add__ = _vadd; V.__truediv__ = _vdiv
V.__iter__ = lambda self: iter((self.x, self.y, self.z))

class BB:
    def __init__(self, s):
        from OCP.Bnd import Bnd_Box; from OCP.BRepBndLib import BRepBndLib
        b = Bnd_Box(); BRepBndLib.Add_s(s, b); x0,y0,z0,x1,y1,z1 = b.Get()
        self.DiagonalLength = ((x1-x0)**2+(y1-y0)**2+(z1-z0)**2)**0.5

class Surface:
    def __init__(self, f): self.s = BRep_Tool.Surface_s(f)
    def parameter(self, p):
        pr = GeomAPI_ProjectPointOnSurf(gp_Pnt(p.x, p.y, p.z), self.s); return pr.LowerDistanceParameters()
    def value(self, u, v):
        q = self.s.Value(u, v); return V(q.X(), q.Y(), q.Z())

class Shape:
    def __init__(self, s): self.s = s
    def isNull(self): return self.s.IsNull()
    @property
    def Faces(self): return [Shape(f) for f in B.faces_of(self.s)]
    @property
    def BoundBox(self): return BB(self.s)
    @property
    def Surface(self): return Surface(self.s)
    def tessellate(self, tol):
        BRepMesh_IncrementalMesh(self.s, tol, False, 0.5, True)
        loc = TopLoc_Location(); tri = BRep_Tool.Triangulation_s(self.s, loc)
        verts = [V(*(lambda p: (p.X(), p.Y(), p.Z()))(tri.Node(i))) for i in range(1, tri.NbNodes()+1)]
        tris = [tuple(x-1 for x in tri.Triangle(i).Get()) for i in range(1, tri.NbTriangles()+1)]
        return verts, tris
    def distToShape(self, other):
        d = BRepExtrema_DistShapeShape(self.s, other.s); return (d.Value(),)

def Compound(shapes):
    c = TopoDS_Compound(); b = BRep_Builder(); b.MakeCompound(c)
    for s in shapes: b.Add(c, s.s)
    return Shape(c)
def Vertex(v): return Shape(BRepBuilderAPI_MakeVertex(gp_Pnt(v.x, v.y, v.z)).Vertex())

part = types.ModuleType("Part"); part.Shape = Shape; part.Compound = Compound; part.Vertex = Vertex
sys.modules["Part"] = part
