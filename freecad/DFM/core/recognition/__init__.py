# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Feature recognition for B-rep solids (pure OCP, no FreeCAD).

Recognises holes (simple, counterbored, countersunk, counterdrilled,
stepped, threaded), bosses (solid, screw, stepped, column), ribs and
gussets, slots, steps, pockets, cutouts, fillets, rounds and chamfers;
and, for a pull or print direction, drafted walls, undercuts and FDM
concerns. See DESIGN.md for the method and README.md for parameters.

Usage::

    from freecad.DFM.core.recognition import recognize, FeatureKind

    result = recognize(shape)                  # a TopoDS_Shape
    for hole in result.of_kind(FeatureKind.HOLE):
        print(hole.face_labels, hole.params["diameter"], hole.confidence)
    result.owner_of(12)                        # the feature made of Face12

    report = annotate_directional(result, shape, pull=(0, 0, 1))

Face and edge numbers are 1-based, as in ``core.utils.geometry.FaceIndex``
and FreeCAD's ``Face<n>``. Results are plain data: they pickle, and
``to_dict()`` / ``from_dict()`` round-trip through JSON.

Recognition runs once per shape; use ``FeatureCache`` (keyed on the
shape's geometric fingerprint, the options and the engine version) to
share a result between analyzers and checks.
"""

from ._version import ENGINE_VERSION
from .api import recognize
from .cache import FeatureCache, fingerprint
from .directional import annotate_directional
from .hints import Hint
from .model import DirectionalReport, Feature, FeatureKind, FaceSignature, Frame, RecognitionResult
from .options import RecognitionOptions, options_from_prefs, pref_key

__all__ = [
    "ENGINE_VERSION",
    "recognize",
    "annotate_directional",
    "RecognitionOptions",
    "RecognitionResult",
    "DirectionalReport",
    "Feature",
    "FeatureKind",
    "FaceSignature",
    "Frame",
    "Hint",
    "FeatureCache",
    "fingerprint",
    "options_from_prefs",
    "pref_key",
]
