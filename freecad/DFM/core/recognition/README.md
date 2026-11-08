# core/recognition: feature recognition for B-rep solids

Pure OCP (cadquery-ocp). No FreeCAD, Part or Qt imports, which a test
enforces. It takes a `TopoDS_Shape` and finds holes, bosses, ribs, slots,
steps, pockets, cutouts, internal voids, fillets and chamfers. For a pull or print
direction, it also finds drafted walls, undercuts and 3D-printing concerns.
DESIGN.md describes the method and its sources.

```python
from freecad.DFM.core.recognition import recognize, annotate_directional, FeatureKind

result = recognize(shape)                       # TopoDS_Shape
for f in result.of_kind(FeatureKind.HOLE):
    print(f.label, f.face_labels, f.params["diameter"], f.confidence)
result.owner_of(12)                             # the feature made of Face12, or None
result.on_face(12)                              # features owning or standing on Face12
report = annotate_directional(result, shape, pull=(0, 0, 1), print_direction=(0, 0, 1))
```

* **Face and edge numbers** are 1-based, from `core.utils.geometry.FaceIndex` / `EdgeIndex`.
  They are FreeCAD's `Face<n>` / `Edge<n>`.
* **Results are plain data.** They pickle, `to_dict()` / `from_dict()` round-trip through
  JSON, and they are deterministic for a given shape, options and `ENGINE_VERSION`.
* **Caching:** `FeatureCache().get(shape, options, hints)` keys results by the shape's
  geometric fingerprint, the options and the engine version.
* **Thresholds:** every threshold is a field of `RecognitionOptions`. Their labels, units,
  ranges and groups build the workbench's *Preferences > DFM > Feature Recognition* page.
* **Hints:** `recognize(shape, hints=[Hint(...)])` takes what a model's history says.
  The workbench builds hints from PartDesign features (`app/recognition_hints.py`).
  * A hint that agrees with a feature raises its confidence and adds design data, such as
    a cosmetic thread.
  * A hint that disagrees lowers the confidence.
  * A single-kind hint with no feature on its faces becomes a feature of its own.
* **Errors and aborts:** `strict=True` makes a recognizer error raise. By default the
  recognizer is skipped and a warning is added to the result. `check_abort` stops a run
  with `InterruptedError`.

## Features and their parameters

Units are mm and degrees. A feature *owns* `faces`, and each face is owned by at most
one feature. It *uses* `support_faces`, which are the faces it stands on or opens into.

| Kind | Subtypes | Parameters |
|---|---|---|
| hole | simple, counterbored, countersunk, counterdrilled, stepped, threaded | profile (`teardrop`, with point_angle), crush_ribs, diameter, depth, through, cb2_diameter, cb2_depth (counterbored from both ends), wall_taper_deg (drafted bores), bottom (`none`/`flat`/`drill_point`/`spherical`), bottom_angle, total_depth, bottom_radius, cb_diameter, cb_depth, cs_diameter, cs_angle, step_diameters, partial, span_deg, axis, threaded, thread_major_diameter, thread_minor_diameter, thread_pitch, thread_modeled (+ design_* and thread_size from history) |
| boss | solid, screw, stepped, column, pin (solid, at least `min_pin_slenderness` diameters tall) | slenderness, wall_faces (side walls it is tied into), outer_diameter, height, taper_deg, root_fillet_radius, step_diameters, inner_diameter, wall_thickness, inner_hole, gussets, gusset_count, base_faces, axis |
| rib | single, sloped, gusset, network, crush (inside a bore; parent = the hole) | interference, bore_diameter (crush ribs), thickness, thickness_root, thickness_tip, height, length, height_to_thickness, draft_deg_side1, draft_deg_side2, tip (`flat`/`round`/`sloped`/`curved`/`flush`: its top is the surface around it, kept as support), root_fillet_radius, ends (`free`/`wall`/`boss`/`rib`), base_faces |
| slot | open, open_end, obround, closed, saddle (a round cradle across walls), ring (an O-ring or rim groove, closed on itself; drafted walls allowed), nut_slot (a nut pushed in from the side across a bolt hole) | inner_diameter / outer_diameter (round ring grooves), nut_thickness, nut_width, nut_size, bolt_hole, width, depth, length, overall_length, end_radius, open_ends, through, profile (`rectangular`/`v`/`polygonal`/`slit`) |
| step | (none), blind | depth, width, length, open_ends, walls |
| pocket | flat, general, stepped, nut_trap (hexagonal, on a hole's axis) | across_flats, nut_size (nearest ISO 4032 nut), bolt_hole, depth, floor_area, shape, wall_count, min_corner_radius, wall_taper_deg, levels, pierced |
| cutout | through, stepped | thickness / depth, shape, wall_count / walls, min_corner_radius, wall_taper_deg, levels |
| void | internal, vented | max_size, min_size, face_count, escape_holes, min_escape_diameter (a cavity inside the part; vented when holes lead out of it, so resin or powder can drain) |
| fillet | concave (fillet), convex (round) | radius, radius_min, radius_max, face_count, span_deg |
| chamfer | convex, concave | width, width_max, distance1, distance2, angle, face_count |
| protrusion / depression | block, general | height / depth, face_count (residual regions, low confidence) |
| snap_fit | cantilever | thickness (at the root), thickness_tip, length, width, overhang, height, length_to_thickness, draft_deg, lead_in_deg, mould_window (an opening under the catch, so a straight-pull core can form it) |
| living_hinge | single, double (grooved from both sides) | web_thickness, length, groove_width, sides, profile |
| vent | slots, holes, openings | count, pitch, opening_width, opening_length, wall_thickness, open_area (an evenly spaced group of through openings) |
| lip | outer, inner | height, thickness, length, position (a thin wall standing on a housing rim, for locating the mating half) |
| text | engraved, embossed | glyphs, depth, glyph_size, min_stroke, extent (lettering or a logo; the letters merge into one feature) |
| draft *(directional)* | positive, negative, mixed | draft_min_deg, draft_max_deg, direction |
| undercut *(directional)* | feature, slide, unreleased | feature, feature_kind, side_action, released |
| overhang *(directional, print)* | flat, sloped | max_angle_from_horizontal_deg, height_above_bed, face_count (down-facing beyond `fdm_overhang_angle`, off the bed) |
| bridge *(directional, print)* | flat | span, height_above_bed, supported_sides, too_long (span over `fdm_max_bridge`) |

Besides features, `result.form` says how the whole part could be modelled
(the base feature a parametric rebuild would start from): `extrusion`
(one profile through), `2.5d` (an extrusion with extruded cuts and bosses
along the same direction), `revolve`, or `mixed`. It also gives the
direction, the share of the area it explains, the number of levels, the
largest wall draft, and the faces it doesn't explain.

`parent` links a hole to its boss and a fillet chain to the feature it rounds. A gusset
is linked to its boss. `interacts_with` lists the features that use each other's faces.

### Directional annotations (`annotate_directional`)

`report.annotations[feature_id]` holds the following.

* **Pull direction:**
  * draft_min_deg and draft_max_deg of the feature's walls
  * needs_draft
  * axis_angle_to_pull_deg and along_pull, for holes and bosses
* **Print direction:**
  * holes: horizontal, teardrop_recommended, below_min_printable, print_axis_angle_deg
  * ribs: thin_for_nozzle, perimeters
  * bosses: fragile_pin, thin_wall_for_nozzle
  * chamfers and fillets on the bed edge: bed_edge_relief
  * holes, pockets and cutouts that cup in a resin printer: cup ("tank" when
    open towards the tank's film, "platform" when sealed by the build
    platform) and cup_area (the opening, mm²). Recesses meeting an opening
    through the part are vented and left out.
  * new features: overhang and bridge (above)

`report.face_draft[face]` is each face's signed draft, asin(n · pull).

## Tests

```sh
# unit tests + every fixture in every variant (plain Python, cadquery-ocp only)
python -m unittest discover -s freecad/DFM/core/recognition/tests -t .
# the suite with a report, plus the approved corpus
python -m freecad.DFM.core.recognition.testing.run --corpus tests/recognition/corpus -v
# write every fixture part (BREP + STEP) and its ground truth to a folder
python -m freecad.DFM.core.recognition.testing.run --export /tmp/fixtures
# score against the MFCAD dataset (STEP + .face_truth per part; labels are
# parsed without unpickling), drawing the worst parts beside their truth
python -m freecad.DFM.core.recognition.testing.mfcad /path/to/MFCAD/step --render /tmp/worst
```

`testing/render.py` draws any shape with its features colour-coded. It can
draw a ground-truth colouring beside it, which is how results on real
models were judged (DESIGN.md section 14).

`testing/fixtures.py` holds 109 parametric parts, each built with OCP on every run.
Each part has ground truth chosen geometrically, by selectors rather than face numbers.

Every part runs in four variants, each mapped back to the original's faces:

* as built;
* rotated and moved;
* converted to all-B-spline surfaces;
* round-tripped through STEP.

The workbench's developer bench runs the same suite (*DFM > Developer > Feature
Recognition Bench*). Use it to inspect results and to approve real parts into
`tests/recognition/corpus`.
