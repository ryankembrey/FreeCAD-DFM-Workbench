# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Recognition options: every threshold the recognizers use.

Each field carries metadata (label, unit, range, tooltip, group) that the
workbench uses to build its preferences page, so a new threshold added here
shows up in the preferences without GUI changes. The preference key of a
field is ``"Recognition" + PascalCase(name)`` (see ``pref_key``).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields, asdict, replace
from typing import Any


def _opt(default, label, unit="", lo=0.0, hi=1e6, decimals=2, tip="", group="General"):
    return field(default=default, metadata={
        "label": label, "unit": unit, "min": lo, "max": hi, "decimals": decimals,
        "tooltip": tip, "group": group,
    })


@dataclass(frozen=True)
class RecognitionOptions:
    # -- Tolerances ------------------------------------------------------------
    linear_tolerance: float = _opt(
        1e-3, "Linear tolerance", "mm", 1e-6, 1.0, 5,
        "Distances closer than this are equal (coaxial, coplanar...).", "Tolerances")
    smooth_angle: float = _opt(
        0.5, "Smooth edge angle", "°", 0.01, 10.0, 2,
        "Faces meeting at less than this angle are tangent (a smooth edge).", "Tolerances")
    parallel_angle: float = _opt(
        1.0, "Parallel angle", "°", 0.01, 10.0, 2,
        "Axes and normals closer than this angle are parallel.", "Tolerances")
    canonical_tolerance: float = _opt(
        1e-3, "B-spline fit tolerance", "mm", 1e-6, 1.0, 5,
        "B-spline faces within this distance of a plane, cylinder, cone or sphere\n"
        "are treated as that surface (common in STEP imports).", "Tolerances")
    edge_samples: int = _opt(
        5, "Samples per edge", "", 1, 25, 0,
        "Points per edge at which the angle between its faces is measured.", "Tolerances")

    # -- Holes ---------------------------------------------------------------
    min_hole_span: float = _opt(
        180.0, "Least arc of a partial hole", "°", 1.0, 360.0, 1,
        "A hole cut away by another feature still counts if this much of its\n"
        "circle is left.", "Holes")
    max_bore_taper: float = _opt(
        10.0, "Largest taper of a drafted bore", "°", 0.0, 30.0, 1,
        "A cone in a hole leaning less than this from the axis is a drafted\n"
        "(moulded) bore, not a countersink or chamfer.", "Holes")
    max_hole_diameter: float = _opt(
        0.0, "Largest hole diameter", "mm", 0.0, 1e5, 2,
        "Larger internal cylinders are not holes. 0 = no limit.", "Holes")
    drill_point_angle: float = _opt(
        118.0, "Drill point angle", "°", 60.0, 180.0, 1,
        "Included angle of a standard drill point (for matching cone bottoms).", "Holes")

    # -- Bosses -------------------------------------------------------------
    max_boss_taper: float = _opt(
        10.0, "Largest boss taper", "°", 0.0, 45.0, 2,
        "Cones steeper than this are not boss walls.", "Bosses")
    max_boss_diameter: float = _opt(
        0.0, "Largest boss diameter", "mm", 0.0, 1e5, 2,
        "Larger external cylinders are not bosses. 0 = no limit.", "Bosses")

    # -- Ribs -----------------------------------------------------------------
    max_rib_thickness: float = _opt(
        6.0, "Largest rib thickness", "mm", 0.01, 1e4, 2,
        "Thicker parallel walls are not ribs.", "Ribs")
    min_rib_aspect: float = _opt(
        1.5, "Least rib height / thickness", "", 0.1, 100.0, 2,
        "Lower, wider walls are not ribs.", "Ribs")
    min_rib_length_ratio: float = _opt(
        2.0, "Least rib length / thickness", "", 0.1, 100.0, 2,
        "Shorter walls are posts or pins, not ribs.", "Ribs")
    max_rib_draft: float = _opt(
        5.0, "Largest rib side draft", "°", 0.0, 30.0, 2,
        "Rib sides may lean by up to this much each.", "Ribs")

    # -- Slots, steps, pockets -----------------------------------------------
    max_slot_width: float = _opt(
        0.0, "Largest slot width", "mm", 0.0, 1e5, 2,
        "Wider channels are not slots. 0 = no limit.", "Slots and pockets")

    # -- Blends -----------------------------------------------------------------
    max_fillet_radius: float = _opt(
        25.0, "Largest fillet radius", "mm", 0.01, 1e4, 2,
        "Larger part-cylinders between two faces are not fillets.", "Fillets and chamfers")
    max_blend_span: float = _opt(
        179.0, "Largest fillet arc", "°", 1.0, 180.0, 1,
        "A fillet turns through less than this. Half-round ends (180°) are\n"
        "left to ribs and slots.", "Fillets and chamfers")
    max_chamfer_width: float = _opt(
        25.0, "Largest chamfer width", "mm", 0.01, 1e4, 2,
        "Wider bevel faces are not chamfers.", "Fillets and chamfers")
    min_chamfer_angle: float = _opt(
        10.0, "Least chamfer angle", "°", 1.0, 89.0, 1,
        "A chamfer meets each of its faces at between this and 90° minus it.",
        "Fillets and chamfers")

    # -- Directional ---------------------------------------------------------
    min_draft: float = _opt(
        0.25, "Least draft", "°", 0.0, 10.0, 2,
        "Walls drafted less than this are taken as having no draft.", "Draft and undercuts")
    max_draft: float = _opt(
        15.0, "Largest wall draft", "°", 0.1, 45.0, 2,
        "Faces leaning more than this are not drafted walls.", "Draft and undercuts")
    undercut_samples: int = _opt(
        3, "Undercut samples per face side", "", 1, 15, 0,
        "Grid size of the ray tests that find undercut faces.", "Draft and undercuts")
    fdm_min_hole_nozzles: float = _opt(
        2.0, "Least printable hole (nozzles)", "", 0.5, 20.0, 1,
        "Holes narrower than this many nozzle widths are flagged.", "3D printing")
    fdm_min_wall_nozzles: float = _opt(
        2.0, "Least printable rib (nozzles)", "", 0.5, 20.0, 1,
        "Ribs and walls thinner than this many nozzle widths are flagged.", "3D printing")
    fdm_teardrop_diameter: float = _opt(
        5.0, "Teardrop from hole diameter", "mm", 0.0, 1000.0, 1,
        "Horizontal holes at least this wide are flagged for a teardrop shape.",
        "3D printing")

    fdm_overhang_angle: float = _opt(
        45.0, "Overhang angle", "°", 0.0, 89.0, 1,
        "Faces facing down further than this from vertical need support.", "3D printing")
    fdm_max_bridge: float = _opt(
        10.0, "Longest unsupported bridge", "mm", 0.0, 1000.0, 2,
        "Downward-facing flat spans longer than this between supports are flagged.", "3D printing")
    min_pin_slenderness: float = _opt(
        3.0, "Pin height / diameter", "", 1.0, 100.0, 1,
        "A solid boss at least this many diameters tall is a pin (fragile to print and mould).",
        "3D printing")

    # -- Moulding details ----------------------------------------------------------
    max_hinge_thickness: float = _opt(
        0.6, "Thickest living hinge web", "mm", 0.0, 10.0, 2,
        "A groove leaving a web this thin (or thinner) is a living hinge.", "Moulding details")
    max_snap_thickness: float = _opt(
        5.0, "Thickest snap-fit beam", "mm", 0.0, 100.0, 2,
        "Cantilevers thicker than this are not snap-fit beams.", "Moulding details")
    min_vent_count: int = _opt(
        3, "Fewest openings in a vent", "", 2, 100, 0,
        "This many like openings in a row or grid through one wall make a vent.", "Moulding details")
    max_text_depth: float = _opt(
        2.0, "Deepest text", "mm", 0.0, 100.0, 2,
        "Engraved or embossed shapes deeper or taller than this are not lettering.", "Moulding details")
    min_text_glyphs: int = _opt(
        3, "Fewest glyphs in text", "", 2, 100, 0,
        "This many small shallow shapes on one face make text.", "Moulding details")
    max_lip_height: float = _opt(
        6.0, "Tallest lip", "mm", 0.0, 100.0, 2,
        "A wall standing round a housing's rim taller than this is not a locating lip.", "Moulding details")

    # -- Hints and reporting ------------------------------------------------------
    use_history_hints: bool = _opt(
        True, "Use the model's history as hints", "", 0, 1, 0,
        "Holes, fillets, chamfers, pockets and pads from the PartDesign tree\n"
        "confirm what is recognised and fill in what geometry can't show\n"
        "(cosmetic threads, design diameters).", "Hints")
    report_residuals: bool = _opt(
        True, "Report unrecognised regions", "", 0, 1, 0,
        "Concave regions no recognizer claims are listed as depressions and\n"
        "protrusions, with low confidence.", "Hints")
    min_confidence: float = _opt(
        0.5, "Least confidence for checks", "", 0.0, 1.0, 2,
        "Checks only use features recognised at least this confidently.", "Hints")

    #: Recognizers to skip, by name ("holes", "bosses", ...). Not a preference.
    disabled: tuple[str, ...] = field(default=(), metadata={"internal": True})

    # -- Helpers ------------------------------------------------------------------

    def to_dict(self) -> dict:
        d = asdict(self)
        d["disabled"] = list(self.disabled)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "RecognitionOptions":
        known = {f.name: f for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for k, v in (d or {}).items():
            if k not in known:
                continue
            if k == "disabled":
                kwargs[k] = tuple(v)
            else:
                kwargs[k] = type(known[k].default)(v)
        return cls(**kwargs)

    def key(self) -> str:
        """A short stable hash, for cache keys."""
        text = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha1(text.encode()).hexdigest()[:16]

    def with_(self, **changes) -> "RecognitionOptions":
        return replace(self, **changes)

    def enabled(self, recognizer: str) -> bool:
        return recognizer not in self.disabled

    @staticmethod
    def preference_fields():
        """The fields shown in the preferences, with their metadata."""
        return [f for f in fields(RecognitionOptions) if not f.metadata.get("internal")]


def pref_key(name: str) -> str:
    """Preference key for an option field: max_rib_thickness ->
    RecognitionMaxRibThickness."""
    return "Recognition" + "".join(part.capitalize() for part in name.split("_"))


def options_from_prefs(prefs: dict) -> RecognitionOptions:
    """Options from a dict of preference values keyed by pref_key()."""
    values = {}
    for f in RecognitionOptions.preference_fields():
        key = pref_key(f.name)
        if key in prefs and prefs[key] is not None:
            values[f.name] = prefs[key]
    return RecognitionOptions.from_dict(values)
