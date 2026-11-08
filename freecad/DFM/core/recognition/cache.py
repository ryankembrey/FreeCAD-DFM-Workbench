# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Shape fingerprints and the per-shape result cache.

Recognition runs once per shape. The cache key is the shape's geometric
fingerprint (stable across processes and sessions, unlike TopoDS hash
codes), the options and the engine version, so a changed shape, changed
thresholds or a new engine all miss the cache.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Iterable, Optional

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS, TopoDS_Shape

from ._version import ENGINE_VERSION
from .options import RecognitionOptions


def fingerprint(shape: TopoDS_Shape) -> str:
    """SHA-1 over face/edge counts and each face's type, area and centroid
    (in face-number order, rounded to 1e-4)."""
    faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, faces)
    edges = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, edges)
    h = hashlib.sha1()
    h.update(f"{faces.Extent()}:{edges.Extent()}".encode())
    for i in range(1, faces.Extent() + 1):
        face = TopoDS.Face_s(faces.FindKey(i))
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        c = props.CentreOfMass()
        kind = int(BRepAdaptor_Surface(face, False).GetType())
        h.update(f"|{kind}:{abs(props.Mass()):.4f}:{c.X():.4f}:{c.Y():.4f}:{c.Z():.4f}:{int(face.Orientation())}".encode())
    return h.hexdigest()


class FeatureCache:
    """A small LRU of recognition results keyed by fingerprint + options."""

    def __init__(self, maxsize: int = 8):
        self.maxsize = maxsize
        self._data: "OrderedDict[tuple, object]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(fp: str, options: RecognitionOptions, hints_key: str = "") -> tuple:
        return (fp, options.key(), ENGINE_VERSION, hints_key)

    def get(self, shape: TopoDS_Shape, options: Optional[RecognitionOptions] = None,
            hints: Iterable = (), **kwargs):
        """The result for *shape*, recognising it on a miss."""
        from .api import recognize

        options = options or RecognitionOptions()
        hints = tuple(hints)
        hk = hashlib.sha1(repr([h.to_dict() for h in hints]).encode()).hexdigest()[:12] if hints else ""
        fp = fingerprint(shape)
        k = self.key(fp, options, hk)
        with self._lock:
            if k in self._data:
                self._data.move_to_end(k)
                self.hits += 1
                return self._data[k]
        result = recognize(shape, options, hints=hints, **kwargs)
        with self._lock:
            self.misses += 1
            self._data[k] = result
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)
        return result

    def put(self, shape_fingerprint: str, options: RecognitionOptions, result, hints_key: str = "") -> None:
        with self._lock:
            self._data[self.key(shape_fingerprint, options, hints_key)] = result
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        return len(self._data)
