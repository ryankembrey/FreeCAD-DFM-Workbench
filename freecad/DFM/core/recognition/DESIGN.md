# Feature recognition engine: design and build plan

Status: **implemented (engine 1.0.0)**. Section 13 records where the build
differs from this plan and what is still open.
Location: `freecad/DFM/core/recognition/` (pure OCP, self-contained, own tests).

---

## 1. Summary

**Recommendation: a hybrid, graph-first engine.** Build an Attributed
Adjacency Graph (AAG) with convex / concave / smooth edges. Run blend
(fillet/chamfer) recognition first and *virtually suppress* blends in the
graph. Then run a set of **hint-based recognizers** (holes, bosses, ribs,
slots, steps, pockets) that find features from partial traces and complete
them from geometry (axes, offsets, extents), not from exact topology.
Overlaps are settled by an arbitration pass that assigns face ownership
and records which features interact. A second, cheap **direction-dependent
pass** adds draft, undercut and FDM print-direction annotations for a
given pull or print direction.

Full volume decomposition (cell-based / maximal volumes) and learned
(GNN) recognizers are **not** part of the core. Volume operations are used
only as local checks: point classification, ray depth, and through vs
blind. The data model leaves room to add a learned recognizer later as one
more plugin.

**Recognition is a shape-level service, not an analyzer.** It runs once per
shape and is cached by a geometric fingerprint. Analyzers and checks
receive the result as plain data (face numbers, floats, strings), which
pickles cheaply to the worker processes.

---

## 2. What the workbench already constrains

| Finding | Consequence for the design |
|---|---|
| Analyzers are per-item: `prepare()` once per process, then `analyze(face)`. Faces are batched across worker processes (`base_analyzer.py`, `app/parallel`). | Recognition is a global graph computation, so it doesn't fit `analyze(face)`. Wrapped as an analyzer, it would run inside `prepare()` in **every worker** and cost N times as much. → Make it a shape-level service and pass the *result* to analyzers. |
| Face numbering comes from `core/utils/geometry.FaceIndex` (`TopExp.MapShapes` into a `TopTools_IndexedMapOfShape`, 1-based, the same as FreeCAD `FaceN`). | Every face reference in the API is a 1-based `int` from `FaceIndex`. Edges use `EdgeIndex` numbers. Features never hold `TopoDS` objects. |
| `import freecad.DFM.core.<anything>` runs `core/__init__.py`, which imports `registries`. `process_registry` imports `FreeCAD` and `yaml`. **Today nothing under `core` can be imported in plain Python.** The worker gets around this with FreeCAD stand-ins (`worker._install_stand_ins`). | Plain-Python tests need **one small change outside the package**: make `core/__init__.py` free of side-effect imports, and have the registries import `checks`/`analyzers` lazily on first lookup. Verified: with an empty `core/__init__.py`, `FaceIndex` imports and works under `python -I` with only cadquery-ocp installed, and FreeCAD is never loaded. See Decision 1. |
| `core/utils/geometry.py` is pure OCP. `core/utils/conversion.py` imports `Part`. `core/utils/mold.py` is pure OCP. | Recognition may import `utils.geometry` (FaceIndex, EdgeIndex, normals, UV helpers) and possibly `utils.mold` in the directional pass. It never imports `conversion`. An import-purity test enforces this. |
| `.gitignore` ignores `*.step`/`*.stp`. | The test corpus is stored as `.brep` (OCP-native, written with `BinTools`/`BRepTools`), not STEP. |
| A "Developer Options" group already exists in `DFMPreferencesGeneral`. | The test bench's on/off switch goes there. |
| OCP 7.8.1 has `BRepOffset_Analyse` (edge concavity), `ShapeAnalysis_CanonicalRecognition` (B-spline → plane/cylinder/cone/sphere) and `BinTools`. | `BRepOffset_Analyse` is the **test oracle** for our own convexity code. Canonical recognition handles STEP files whose cylinders arrive as B-splines. Checked on a drilled block: the hole lip came out convex, the hole bottom concave and the seam "other", as expected. |

---

## 3. Research: established approaches and how they fit

**Graph-based (AAG).** Joshi & Chang (1988) introduced the Attributed
Adjacency Graph: faces are nodes, shared edges are arcs labelled convex or
concave, and features are recognized as subgraphs. Their implementation
was limited to polyhedral features, with cylindrical holes handled
separately. Their key heuristic is that a face whose neighbours all meet it
at convex angles isn't part of a (depression) feature. Removing those faces
breaks the graph into small candidate components. Extensions such as Gao &
Shah's EAAG add edge attributes (real/virtual, curved/straight, smooth
blend, inner/outer loop) and face attributes (planarity, number of loops,
convex hull). General subgraph matching (Ullmann, VF2) is exponential in
the worst case. The Analysis Situs authors note it stays practical only
with node elimination and candidate pruning heuristics.
*Strengths:* formal, fast after pruning, easy to test. *Weaknesses:*
intersecting features break the patterns, variable-topology features
(n-sided pockets) can't be one pattern, and different features can share a
graph.

**Hint-based.** Vandenbrande & Requicha proposed searching for the
*minimal indispensable part* of a feature's boundary (a hint) instead of the
whole pattern. For example, two opposing planar faces hint at a slot. Each
hint is then completed and verified geometrically, with interactions
represented explicitly. Han & Requicha extended this with a blackboard
architecture and uncertain reasoning; the 1998 status report compares
IF², F-Rex and FBMach. Gao & Shah unified the two families: isolated
features come from graph matching, and interacting features from a
*minimal condition subgraph* hint completed with virtual links.
*Strengths:* robust to interactions, which is the hard case.
*Weaknesses:* hint and completion code is per feature type, and too many
hints lead nowhere unless they are filtered (hence hybrids).

**Volume decomposition.** Sakurai & Dave decompose the delta volume
(stock minus part) into cells and recombine them into maximal volumes. Woo
& Sakurai (2002) recognize maximal features this way, and Woo's fast
volume decomposition (2003) speeds it up with localized face extension and
seed cells. Cell recombination is exponential in the number of cells, and
the fast variant can return wrong maximal volumes.
*Verdict:* the right tool for machining process planning (alternative
interpretations of removal volumes). It is overkill and fragile for DFM of
moulded or printed parts in Python on OCP: every step is a Boolean. Use
only local volume checks.

**Blend recognition and suppression.** Recognizing fillets and suppressing
them before other recognition is standard practice (Zhu & Menq 2002). Blend
recognition finds blend faces, groups them into chains, and estimates
radius and creation order (Venkataraman, Sohoni & Elber 2001). Slyadnev &
Turlapov (2020) build an AAG enriched with edge types and assumed blend
kinds, then compose candidate blend faces into chains on OpenCascade.
Moulded parts are full of fillets: rib roots, boss roots, corners. **This
step is what makes graph recognition work on real injection-moulded
parts.**

**Moulding-specific.** Lockett & Guenov (2005) recognize ribs, buttresses,
bosses, holes and wall junctions on a mid-surface adjacency graph, and
argue it suits thin-walled parts better than the B-rep. Their prototype
relied on a commercial mid-surfacer, which we don't have. Lai, Wang et al.
recognize ribs on thin-shell parts from *parallel pairs* of faces and
edges, bosses via tube recognition, and classify protrusions into tubes,
ribs, columns, blocks and irregular extrusions after separating inner and
outer shell faces. Fu, Fuh & Nee (1999) define and classify undercut
features and their draw directions. Commercially, CADEX MTK's moulding
recognizer finds ribs, bosses and screw bosses with tunable rib thickness,
draft and taper limits. That is a good model for our options object.

**Learned (GNN).** AAGNet runs a GNN on a geometric AAG and performs
semantic, instance and bottom-face segmentation together. It was trained
and evaluated on MFCAD, MFCAD++ and the 60k-part MFInstSeg set. These
datasets are synthetic *machining* features, so not moulding ones, and the
approach needs torch and training data.
*Verdict:* out of scope now. The AAG serializer is designed so that a
learned recognizer, or a MFInstSeg-based benchmark of the shared feature
types, can be added later.

**Conclusion.** No single method covers the scope. The literature points
to graph + hint hybrids (Gao & Shah; Sunil, Agarwal & Pande 2010) with
blend suppression first. That is the recommendation, adapted to moulding
and FDM: protrusions (ribs, bosses) matter as much as depressions, and
direction (pull or print) is a first-class input.

---

## 4. Architecture

```
TopoDS_Shape
   │
   ▼
[1] Topology & descriptors ─ FaceIndex/EdgeIndex numbering; per-face surface
   │                          kind (+ canonical recognition of B-splines),
   │                          axis/radius/normal, internal vs external
   │                          (concave/convex) cylinders & cones, loops,
   │                          area, angular span; per-edge sampled dihedral
   │                          angle → CONVEX / CONCAVE / SMOOTH_CONVEX /
   │                          SMOOTH_CONCAVE / SEAM / VARIABLE
   ▼
[2] AAG  ───────────────────  multigraph, nodes = faces, arcs = shared edges.
   │                          "Logical faces": coaxial/coplanar split faces
   │                          (two half-cylinders, split planes) grouped.
   ▼
[3] Blends & chamfers ──────  recognize fillet/round/chamfer faces, chains,
   │                          vertex blends → FILLET / CHAMFER features.
   │                          Virtual suppression: blend nodes hidden, virtual
   │                          arcs added between support faces with the
   │                          convexity the blend implies.
   ▼
[4] Hint-based recognizers ─  holes → bosses → ribs → slots/steps → pockets/
   │  (on the simplified AAG)  cutouts → generic depression/protrusion.
   │                          Each: hints → hypotheses → geometric
   │                          verification → parameters → evidence.
   ▼
[5] Arbitration ────────────  face ownership (owned vs support faces),
   │                          priorities + confidence, interactions,
   │                          parent/child (hole inside boss, fillet chain
   │                          at rib root).
   ▼
RecognitionResult (plain data, JSON-serializable, cached by fingerprint)
   │
   ▼  (optional, per direction, cheap)
[6] Directional pass ───────  draft per feature wall, DRAFT groups,
                              undercut features, FDM annotations (horizontal
                              holes, small holes/pins vs nozzle, thin ribs).
```

### Package layout

```
core/recognition/
  __init__.py            public API only (re-exports, __all__, docstring)
  DESIGN.md              this document
  README.md              API usage, feature parameter reference
  _version.py            ENGINE_VERSION (bumped on any behaviour change → cache key)
  api.py                 recognize(), annotate_directional()
  model.py               FeatureKind, Feature, Frame, RecognitionResult, DirectionalReport
  options.py             RecognitionOptions (frozen dataclass, hashable)
  cache.py               fingerprint(shape), FeatureCache (LRU)
  _topology/             index.py, descriptors.py, convexity.py, canonical.py, loops.py
  _graph/                aag.py, views.py (components, Joshi–Chang pruning),
                         matching.py (VF2-style, attribute predicates), patterns.py
  _blends/               blends.py (fillets, rounds, vertex blends, chains),
                         chamfers.py, suppress.py (virtual arcs)
  _recognizers/          base.py, holes.py, bosses.py, ribs.py, slots.py,
                         steps.py, pockets.py, generic.py, threads.py
  _reasoning/            arbitration.py, confidence.py, interactions.py
  _directional/          draft.py, undercut.py, fdm.py
  _geom/                 measure.py (extents along axis, ray depth, BRepClass3d,
                         distances), frames.py
  testing/               fixtures.py (parametric parts + geometric ground truth),
                         select.py (face selectors), corpus.py, compare.py
                         (matching + metrics), run.py (headless CLI), report.py
  tests/                 unittest suite (plain Python, cadquery-ocp only)
```

Underscored subpackages are internal. `testing/` is semi-public: both the
headless runner and the GUI bench use it, so both judge results the same
way.

There are **no dependencies beyond cadquery-ocp**. The graph and the VF2
matcher are pure Python (about 200 lines); no networkx or numpy.

---

## 5. Public API (sketch)

```python
from freecad.DFM.core.recognition import (
    recognize, annotate_directional, RecognitionOptions, RecognitionResult,
    DirectionalReport, Feature, FeatureKind, Frame, FeatureCache, ENGINE_VERSION,
)

result = recognize(shape, RecognitionOptions(), progress=cb, check_abort=abort)

result.features                     # tuple[Feature, ...], deterministic order
result.of_kind(FeatureKind.HOLE)    # filtered view
result.on_face(12)                  # features that own or use Face12
result.owner_of(12)                 # the feature that owns Face12, or None
Feature.face_labels                 # ("Face12", "Face13"), FreeCAD sub-element names
result.fingerprint, result.engine_version, result.options, result.timings, result.warnings
result.to_dict() / RecognitionResult.from_dict(d)   # JSON round-trip (cache, goldens, workers)

report = annotate_directional(result, shape, pull=(0, 0, 1), side_actions=(), print_direction=None)
report.feature_params(fid)          # e.g. {"draft_min_deg": 0.8, ...}
report.features                     # DRAFT groups, UNDERCUT features, FDM annotations

cache = FeatureCache(maxsize=8)
result = cache.get(shape, options)  # recognize() on a miss
```

```python
class FeatureKind(str, Enum):
    HOLE, BOSS, RIB, SLOT, STEP, POCKET, CUTOUT, FILLET, CHAMFER,   # shape-level
    DRAFT, UNDERCUT,                                                # directional
    DEPRESSION, PROTRUSION                                          # residual, low confidence

@dataclass(frozen=True)
class Feature:
    id: int                          # 1-based; stable for (shape, options, ENGINE_VERSION)
    kind: FeatureKind
    subtype: str                     # "through", "blind", "counterbored", "screw", "cross", ...
    faces: tuple[int, ...]           # owned faces, FaceIndex numbers (1-based)
    support_faces: tuple[int, ...]   # faces it stands on / opens into (not owned)
    edges: tuple[int, ...]           # key edges (rim, root loop), EdgeIndex numbers
    params: Mapping[str, float | int | str | bool | tuple]   # mm and degrees
    frame: Frame | None              # origin + axis (+ ref dir), model coordinates
    confidence: float                # 0..1
    evidence: tuple[str, ...]        # human-readable reasons (shown in the bench)
    parent: int | None = None        # e.g. hole → boss, fillet → rib
    interacts_with: tuple[int, ...] = ()
```

### Parameters by feature (v1)

| Kind | Subtypes | Parameters |
|---|---|---|
| HOLE | simple, counterbored, countersunk, counterdrilled, stepped, threaded · `through`/`blind` flag · bottom `flat`/`drill_point`/`spherical` | diameter, depth, axis, through, bottom_angle, cb_diameter, cb_depth, cs_diameter, cs_angle, thread_major/minor/pitch (modeled threads), angular_span (<360 means intersected) |
| BOSS | plain, hollow, screw (hollow + hole), with_gussets | outer_diameter, inner_diameter, height, wall_thickness, taper_deg, root_fillet_radius, top_chamfer/round, gusset_ids |
| RIB | single, cross (network node), gusset | thickness_root, thickness_tip, height, length, draft_deg_side1/2, root_fillet_radius, tip_radius, ends (free / wall / boss), height_to_thickness |
| SLOT | through, blind, obround | width, length, depth, end_radius, open_ends |
| STEP | – | width, depth, length |
| POCKET / CUTOUT | rectangular, circular, general · cutout = through window | depth, floor_area, min_corner_radius, wall_taper_deg |
| FILLET | concave (fillet), convex (round), vertex · chain | radius (min/max if variable), chain_length, support_faces |
| CHAMFER | convex, concave | distance1, distance2, angle |
| DRAFT *(directional)* | – | per-wall draft_deg, min/max, direction |
| UNDERCUT *(directional)* | external (slide), internal (lifter), feature-borne (side hole/boss) | release_direction(s), depth, faces |
| DEPRESSION / PROTRUSION | residual | depth/height, area |

**Draft vs taper.** Without a direction, features report *taper*
relative to their own axis or mid-plane (boss cone half-angle, rib wall
angle). That value doesn't depend on any direction and equals the draft
when the feature axis is the pull direction. True draft depends on the
direction and comes from `annotate_directional`. The existing
DraftAnalyzer stays the per-face authority.

---

## 6. Algorithms

### 6.1 Edge convexity (the AAG attribute everything else depends on)
For an edge shared by faces F1 and F2, sample k points along it (default
5, plus the ends trimmed by 5%). At each point take the outward normals
N1 and N2, evaluated through the edge's pcurves (exact UV, no projection
needed) and corrected for face orientation, and the edge tangent T as
oriented in F1's wire. The sign of (N1 × N2)·T gives convex or concave.
The angle between N1 and N2 gives the dihedral angle. Below `smooth_tol`
(0.5°) the edge is SMOOTH. Smooth edges are split into SMOOTH_CONVEX and
SMOOTH_CONCAVE by comparing normal curvatures across the edge, which tells
a round from a fillet. Mixed samples give VARIABLE. An edge with the same
face on both sides is a SEAM: no arc, and the face is flagged periodic.
The arc stores the angle, the class, the edge length and the curve type.
**Oracle test:** agreement with `BRepOffset_Analyse` on every fixture.

### 6.2 Face descriptors and logical faces
Each face gets a surface kind; B-splines go through
`ShapeAnalysis_CanonicalRecognition` within tolerance and record the fit
error, which feeds confidence. Each face also gets:
- axis, radius and half-angle for cylinders and cones;
- an **internal/external flag** (does the face normal point to the axis?),
  which is the main hole vs boss discriminator;
- the plane normal and offset for planes;
- outer and inner loop counts, area, centroid and bounding box;
- the angular span for revolved faces.

Coaxial, equal-radius cylinders joined by smooth or seam edges, and
coplanar planar faces split by an interaction, are grouped into **logical
faces**. The graph can work on logical nodes, while features always report
the original face numbers.

### 6.3 Blends and chamfers (run first)
A candidate blend face is a cylinder, torus, sphere or B-spline with two
"spring" edges that are smooth to its support faces, and it is narrow
across those springs. Its radius comes from the canonical surface, or from
sampled curvature for B-splines. It is concave or convex depending on
whether the blend centre lies outside or inside the material. Chains are
built across "cross" edges between blends of matching radius; spheres and
tori at corners are vertex blends attached to their chains.

A chamfer is a planar or conical face with two non-smooth edges to its
supports at roughly equal angles, a constant width between the two spring
edges, and a width small relative to its neighbours.

**Virtual suppression.** The blend node is hidden. A virtual arc is added
between the two support faces with the convexity the blend implied (a
concave fillet gives a concave arc), tagged `virtual=True, via=feature_id`.
Downstream recognizers then see the sharp-cornered part the designer
started from. Nothing is changed in the shape, so face numbers stay valid.

### 6.4 Recognizers (hints → hypotheses → verify → params → evidence)
- **Holes.** Hint: an internal cylinder or cone (or a logical group).
  Collect coaxial internal cylinders, cones and annular planes along the
  axis into an ordered stack. Then classify the stack: simple; counterbore
  (cylinder, annulus, smaller cylinder); countersink (entry cone, then
  cylinder); stepped. Rims with convex edges are openings; the bottom is a
  concave plane, cone (drill point, ~118°) or sphere, or there is none
  (through). Depth comes from the extents along the axis. A partial span
  means an intersected hole: it is kept if the span is ≥ 180° or the axis
  is confirmed, with reduced confidence. Point classification on the axis
  confirms material removal. **Threads:** modeled helical faces (B-spline
  sweeps whose distance from the axis stays in a band [r_min, r_maj]) are
  tagged THREADED, with a pitch estimated by sampling (phase 6).
- **Bosses.** Hint: an external cylinder or cone whose one end loop is
  concave to a base face, directly or through a virtual arc. Then take the
  top face and any coaxial internal hole (wall thickness = (OD − ID)/2, so
  a screw boss). Taper is the cone half-angle; the root fillet comes from
  the suppressed blend. Ribs that end on the boss are gussets.
- **Ribs** (after Lai et al.). Hint: a *parallel pair* of planar faces
  that are anti-parallel within `2 × max_rib_draft` and face *away* from
  each other. Their projections must overlap, their separation t must be
  ≤ `max_rib_thickness`, and height / t must be ≥ `min_rib_aspect`. Both
  meet a common base through concave arcs (real or virtual), and a convex
  top face or round joins them. Cross ribs: coplanar pairs split at a
  crossing are merged, and crossings recorded as network nodes. The
  parameters are root and tip thickness, height, length, the draft of each
  side, root and tip radii, and the end conditions.
- **Slots.** Hint: a parallel pair facing *each other*, plus a floor that
  is concave to both walls, or no floor (a through slot). Ends are open
  (convex edges), closed by a wall, or half-cylinders (obround).
- **Steps.** A floor and one wall joined by a concave arc, with the other
  boundaries convex (open on at least two sides).
- **Pockets / cutouts** (Joshi–Chang). Remove all-convex faces, take the
  connected depression components, and find a floor whose boundary to a
  closed ring of walls is all concave. A cutout is a wall ring with both
  rims convex (a window: relevant to mould shut-offs). Variable topology
  is handled by rules over the ring, not by one fixed pattern.
- **Generic.** Leftover depression components, and protrusion components
  found the dual way (bounded by a concave root loop), become
  DEPRESSION/PROTRUSION features with low confidence. Nothing is silently
  left out, and the bench shows them as "unrecognized" regions to review.

A **pattern matcher** (VF2-style, with attribute predicates on nodes and
arcs) is available for features that are cleanly topological, and for
user-defined patterns later. It runs only inside pruned components, so N
stays small.

### 6.5 Arbitration and interactions
Each feature *owns* faces (exclusive) and *uses* support faces (shared;
for example, a pocket floor is also a boss base). Ownership conflicts are
settled by priority (blends > holes > bosses > ribs > slots/steps >
pockets > generic), then confidence. Interactions are recorded when two
features share an edge, a virtual arc or a split logical face. Parent and
child links cover a hole in a boss, a fillet chain at a rib root, and a
counterbore's parts.

### 6.6 Confidence
`confidence = rule_score × completeness × geometric_quality × (1 − ambiguity)`
- rule_score: weighted soft constraints; hard constraints are pass/fail.
- completeness: for example the angular span / 360, or whether a floor was
  found.
- geometric_quality: canonical-fit error, deviation from parallel.
- ambiguity: the margin to the best competing hypothesis.

Every factor adds an evidence string. The weights are calibrated on the
approved corpus, and the bench shows a reliability table (predicted vs
observed precision). Suggested tiers are ≥ 0.85 certain, 0.6–0.85 likely,
and < 0.6 tentative. Checks would use `options.min_confidence`.

### 6.7 Directional pass
`annotate_directional(result, shape, pull, side_actions, print_direction)`
works on the cached result and is cheap. It computes the draft of every
feature wall and DRAFT groups (walls deliberately drafted, 0.25–15° off
the pull axis). It groups undercut faces into UNDERCUT features, with
side-action, lifter or feature-borne classes after Fu, Fuh & Nee, and may
reuse the pure-OCP helpers in `core/utils/mold.py`. FDM annotations are
horizontal holes (teardrop candidates), holes and pins below
`k × nozzle_diameter`, and ribs or walls thinner than the line width.

### 6.8 Options (frozen, hashable, part of the cache key)
Linear tolerance (default from the shape's tolerances and bounding box),
angular tolerances (smooth 0.5°, parallel 1°), max_rib_thickness (6 mm),
min_rib_aspect (1.5), max_rib_draft (5°), max_boss_taper (5°),
max_fillet_radius_ratio, min_confidence, enabled recognizers, and B-spline
canonical tolerance. All values are in mm and degrees, the same as FreeCAD
internals.

---

## 7. How it sits alongside the analyzers

**Recommendation: a shape-level service, passed to analyzers and checks
as data.**

1. `AnalysisRunner.run_analysis` gets the result lazily, only if an active
   check or analyzer declares `requires_features = True`. That is a new
   optional class attribute, default False, so **existing analyzers are
   untouched**. The result comes from a `FeatureCache` keyed on
   `(fingerprint(shape), options, ENGINE_VERSION)`. The fingerprint is a
   SHA-1 over face and edge counts plus each face's surface type, rounded
   area and centroid. It is stable across processes and sessions, unlike
   `TopoDS` hashes.
2. It goes into `kwargs["features"]`, so `prepare(**kwargs)` receives it in
   the main process *and* in workers. `measure._encode` / `worker._decode`
   would gain one case for `RecognitionResult` (`to_dict` / `from_dict`).
   It is plain data, so a few KB to pickle and never recomputed per worker.
3. In `analyze(face)`, an analyzer can ask
   `self.features.owner_of(face_index)`, for example to measure a rib's
   thickness or a boss wall, or to skip fillet faces. New checks (rib
   thickness vs nominal wall, boss wall ratio, hole-to-edge distance,
   minimum printable hole) can use features directly.
4. Optionally, recognition runs **in one worker process**, using the
   existing pool and BREP transfer, so a slow or crashing kernel call can't
   freeze or take down FreeCAD. This matches the parallel package's
   philosophy.
5. `shape_observer` already flags stale results when a shape changes. A
   changed shape gives a new fingerprint, so the cache invalidates itself.

Why not an analyzer: the per-item contract, batching and contributions
model don't fit a global graph. In parallel mode, `prepare()` would rerun
recognition in every worker, and the result isn't per-face values but
cross-face structures.

This step (phase 7) only *proposes* the runner hook and an example check.
No existing analyzer changes until you approve it.

---

## 8. Testing

### 8.1 Headless (plain Python, cadquery-ocp only)
- **Unit tests** in `core/recognition/tests/`. They cover convexity vs the
  `BRepOffset_Analyse` oracle, descriptors, logical faces, AAG building
  and serialization, the matcher, blends, each recognizer, arbitration,
  fingerprint stability and the cache.
- **Parametric fixtures** (`testing/fixtures.py`) build parts in OCP:
  - every hole variant, including partial (intersected) holes;
  - plain, hollow, screw, tapered and filleted bosses, and bosses with
    gussets;
  - single, drafted, filleted, round-topped and cross ribs, ribs ending on
    walls or bosses;
  - through, blind and obround slots; steps; pockets and cutouts with
    corner radii; fillet and chamfer chains;
  - **interacting cases**: a hole through a rib, a boss on a rib crossing,
    a slot crossing a pocket, a hole breaking into a slot wall, a
    counterbore meeting a fillet;
  - **negatives**: a plain box, a plain cylinder, a sphere.

  Each fixture carries **geometric ground truth**. Faces are chosen by
  predicates such as `Select.cylinder(r=3, axis=Z, through=(50, 50))`, not
  by face numbers, so expectations survive OCCT renumbering.
- **Invariance tests**: rotation and translation, `BRepBuilderAPI_NurbsConvert`
  (everything turned into B-splines), split periodic faces, and determinism
  (identical result and ordering twice).
- **Purity test**: walks the package's AST and fails on any import of
  `FreeCAD`, `FreeCADGui`, `Part`, or workbench modules outside
  `core/utils/{geometry,mold}`.
- **Corpus / golden tests**: `tests/recognition/corpus/<case>.brep`, plus
  `<case>.expected.json` (approved) and `<case>.meta.json`
  (source, notes, status). Goldens store face numbers **and** face
  signatures (type, area, centroid), so they still match if numbering
  shifts.
- **Metrics** (`testing/compare.py`): features are matched by kind and
  face IoU ≥ 0.5, giving per-kind precision and recall, face IoU,
  parameter error and confidence calibration. A regression is any approved
  feature lost or changed beyond tolerance.
- **Runner**:
  `python -m freecad.DFM.core.recognition.testing.run [--corpus DIR] [--json out] [-k hole]`
  exits non-zero on failure. A thin `tests/test_recognition.py` hooks the
  package suite into the existing `tests/run_tests.py`. A performance
  benchmark records time per face; the targets are < 1 s for 500 faces and
  < 5 s for 5,000.

### 8.2 GUI test bench (developer-only)
- **Gate:** a new checkbox, "Feature recognition test bench", in
  *Preferences → DFM → General → Developer Options*
  (`DeveloperRecognitionBench`, default off). The command
  `DFM_RecognitionBench` is always registered but only added to a
  "DFM Developer" toolbar and menu when the switch is on.
  `IsActive()` also checks it, so turning it off disables the command
  immediately (toolbar removal takes effect when the workbench reloads).
- **Inspect tab:** recognize the selected body (via `freecad_to_ocp`). It
  shows a feature tree grouped by kind with parameters, confidence and
  evidence. Selecting a feature highlights its owned faces and outlines
  its support faces. There is a colour-by-kind overlay with a legend, and
  an **AAG overlay** with edges coloured convex, concave, smooth or
  virtual. Picking a face shows its number, descriptors and
  logical-face group.
- **Suite tab:** runs the **same `run_suite()`** as the headless runner,
  in-process or in a worker. It lists fixtures and corpus cases with
  pass, fail, regression or unreviewed status and a diff of missing,
  extra and changed features, and can open any case in a new document.
- **Approve tab:** mark each feature correct or wrong, fix its kind or
  subtype, add or remove faces by picking, and add missed features by
  picking faces. "Approve" writes the golden JSON and is enabled only when
  every feature has been reviewed. "Add current model to corpus" exports
  `.brep` + an unreviewed snapshot. The review can be exported as a JSON
  or HTML record.
- **Files:** `gui/tools/recognition_bench.py` (command),
  `gui/recognition_bench/{panel,tree_model,scene,approve}.py`, an edit to
  `gui/preferences.py` (checkbox) and an edit to `init_gui.py` (the
  conditional developer toolbar and menu). The bench draws with its own
  small Coin overlay rather than coupling to the results highlight scene.

---

## 9. Phased build plan

Each phase is delivered as a zip of changed files under `dfm/` and ends
with all tests green.

| Phase | Deliverable | Exit criteria |
|---|---|---|
| **0. Skeleton & guard rails** | Package, public API stubs, data model + JSON round-trip, options, fingerprint + cache, fixture and selector framework, purity test, test runner hookup. Make `core/__init__.py` side-effect free (lazy registry loading). | `python -I -m unittest` passes with only cadquery-ocp. The existing workbench behaves the same (registry lookups still find every check). |
| **1. Topology & AAG** | Descriptors, canonical recognition, convexity, logical faces, AAG + views (components, Joshi–Chang pruning), VF2 matcher, serialization | 100% agreement with the `BRepOffset_Analyse` oracle on fixtures; AAG invariant under rotation and NURBS conversion; benchmark baseline |
| **2. Minimal bench (Inspect only)** | Pref gate, command, Inspect tab with AAG overlay and face picking | You can see convexity and descriptors on any FreeCAD body. *(Built early because recognition is hard to debug without seeing it.)* |
| **3. Blends & chamfers** | Fillet, round, vertex-blend and chamfer recognition, chains, virtual suppression | Fixtures pass; suppressed AAG of a filleted box ≡ AAG of the sharp box |
| **4. Holes & bosses** | All hole subtypes (except modeled threads); boss subtypes, gussets as links | Fixtures + interacting cases pass; precision and recall ≥ 0.95 on fixtures |
| **5. Ribs, slots, steps, pockets, generic + arbitration** | Rib networks, slot/step/pocket/cutout, residual features, ownership and interactions, calibrated confidence | Fixture suite passes; no face owned twice; interacting suite passes |
| **6. Bench Suite + Approve; directional pass; threads** | Suite and Approve tabs, corpus format, goldens; `annotate_directional` (draft, undercut, FDM); modeled threads | First 20–30 real parts reviewed and approved in the GUI; headless run reproduces the GUI verdicts |
| **7. Integration proposal** | `requires_features` hook in `AnalysisRunner`, worker encode/decode, optional recognition-in-worker, one example check (for example rib-to-wall thickness or minimum printable hole) | Reviewed by you before merging; existing analyzers unchanged |

Later and optional: external hints from the FreeCAD tree (PartDesign
Hole, Pad, Fillet metadata as extra evidence), a learned recognizer
plugin, an MFInstSeg benchmark for holes, slots, steps, pockets and
chamfers, and alternative interpretations for ambiguous interacting
features.

---

## 10. Risks and mitigations

| Risk | Mitigation |
|---|---|
| STEP imports with B-spline "cylinders" and planes | Canonical recognition with fit error feeding confidence; NURBS-conversion invariance tests |
| Split or periodic faces from other CAD systems | Logical faces; split-face fixtures |
| Fillets everywhere on moulded parts hide topology | Blend recognition and virtual suppression first (phase 3) |
| Interacting features | Hints + geometric completion, virtual arcs, partial-span handling, explicit interaction records, an interacting-case suite |
| Ribs on curved shells and drafted walls | Pair test tolerant to draft; base may be any surface (only the concave arc matters) |
| Face numbering differs between OCCT versions | All references through FaceIndex at runtime; goldens carry face signatures |
| Pure-Python speed on big parts | Per-edge work only; pruned components for matching; benchmark in CI; optional worker execution |
| Threshold sensitivity (what counts as a rib?) | Options object, evidence strings, calibration on the approved corpus; process YAML hookup later |

---

## 11. Decisions (settled)

1. `core/__init__.py` is side-effect free; the registries load the checks
   and analyzers on their first lookup.
2. Any data FreeCAD has may be used as hints, history included. The core
   stays pure OCP; `app/recognition_hints.py` (FreeCAD side) turns
   PartDesign features into plain-data hints.
3. Every threshold is tweakable in Preferences > DFM > Feature Recognition
   (generated from RecognitionOptions).

---

## 12. References

- S. Joshi, T.C. Chang. Graph-based heuristics for recognition of machined features from a 3D solid model. *Computer-Aided Design* 20(2):58–66, 1988. doi:10.1016/0010-4485(88)90050-4
- J.H. Vandenbrande, A.A.G. Requicha. Spatial reasoning for the automatic recognition of machinable features in solid models. *IEEE TPAMI*, 1993.
- J.H. Han, W.C. Regli, S. Brooks. Hint-based reasoning for feature recognition: status report. *Computer-Aided Design* 30(13):1003–1007, 1998.
- J.H. Han, M. Pratt, W.C. Regli. Manufacturing feature recognition from solid models: a status report. *IEEE Trans. Robotics and Automation* 16(6):782–796, 2000. doi:10.1109/70.897789
- S. Gao, J.J. Shah. Automatic recognition of interacting machining features based on minimal condition subgraph. *Computer-Aided Design* 30(9):727–739, 1998. doi:10.1016/S0010-4485(98)00033-5
- H. Sakurai, P. Dave. Volume decomposition and feature recognition, Part 1: polyhedral objects. *Computer-Aided Design* 27:833, 1995; Part 2: curved objects, 1996.
- Y. Woo, H. Sakurai. Recognition of maximal features by volume decomposition. *Computer-Aided Design* 34(3):195–207, 2002. doi:10.1016/S0010-4485(01)00080-X
- Y. Woo. Fast cell-based decomposition and applications to solid modeling. *Computer-Aided Design* 35(11):969–977, 2003. doi:10.1016/S0010-4485(02)00144-6
- B. Babić, N. Nešić, Z. Miljković. A review of automated feature recognition with rule-based pattern recognition. *Computers in Industry* 59(4):321–337, 2008. doi:10.1016/j.compind.2007.09.001
- V.B. Sunil, R. Agarwal, S.S. Pande. An approach to recognize interacting features from B-Rep CAD models of prismatic machined parts using a hybrid (graph and rule based) technique. *Computers in Industry* 61(7):686–701, 2010. doi:10.1016/j.compind.2010.03.011
- S. Venkataraman, M. Sohoni, G. Elber. Blend recognition algorithm and applications. *Proc. 6th ACM Symp. Solid Modeling and Applications*, 99–108, 2001.
- H. Zhu, C.H. Menq. B-Rep model simplification by automatic fillet/round suppressing for efficient automatic feature recognition. *Computer-Aided Design* 34(2):109–123, 2002. doi:10.1016/S0010-4485(01)00056-2
- S.E. Slyadnev, V.E. Turlapov. Simplification of CAD models by automatic recognition and suppression of blend chains. *Programming and Computer Software* 46:233–243, 2020. doi:10.1134/S0361768820030081
- A. Malyshev, S. Slyadnev, V. Turlapov. Graph-based feature recognition and suppression on the solid models. *GraphiCon* 2017, 319–322.
- Analysis Situs blog: useful heuristics for checking AAG isomorphisms (2020); convex hull heuristic. https://quaoar.su/blog
- H.L. Lockett, M.D. Guenov. Graph-based feature recognition for injection moulding based on a mid-surface approach. *Computer-Aided Design* 37(2):251–262, 2005. doi:10.1016/j.cad.2004.06.010
- M.W. Fu, J.Y.H. Fuh, A.Y.C. Nee. Undercut feature recognition in an injection mould design system. *Computer-Aided Design* 31(12):777–790, 1999.
- J.Y. Lai, M.H. Wang, P.P. Song, C.H. Hsu, Y.C. Tsai. Automatic recognition and decomposition of rib features in thin-shell parts for mold flow analysis. *Engineering with Computers* 34(4):801–820, 2018.
- M.H. Wang, J.Y. Lai, C.H. Hsu, Y.C. Tsai, C.Y. Huang. Boss recognition algorithm for application to finite element analysis. *Computer-Aided Design and Applications* 14(4):450–463, 2017.
- J.Y. Lai et al. Recognition and classification of protrusion features on thin-wall parts for mold flow analysis. *Engineering with Computers* 37:833–854, 2021. doi:10.1007/s00366-019-00859-1
- R.A. Bidkar, D.A. McAdams. Feature recognition for injection-molded and die-cast parts. *ASME DETC*, 2004.
- H. Wu, R. Lei, Y. Peng, L. Gao. AAGNet: a graph neural network towards multi-task machining feature recognition. *Robotics and Computer-Integrated Manufacturing*, 2024. doi:10.1016/j.rcim.2023.102661
- J.R. Ullmann. An algorithm for subgraph isomorphism. *J. ACM* 23(1):31–42, 1976.
- L.P. Cordella, P. Foggia, C. Sansone, M. Vento. A (sub)graph isomorphism algorithm for matching large graphs (VF2). *IEEE TPAMI* 26(10):1367–1372, 2004.
- CADEX MTK, Injection moulding feature recognition (rib/boss parameters), product documentation.

---

## 13. As built

**Package layout.** As section 4, with these small changes:

* the directional pass is `directional.py`;
* hints are `hints.py` (public);
* reasoning is `_reasoning.py`;
* threads have their own recognizer (`_recognizers/threads.py`), run after
  holes and bosses.

**Additions found necessary by the tests:**

* **Logical bases for ribs.** Coplanar faces act as one base. A rib running
  wall to wall splits its floor in two, with one side standing on each piece.
* **Rib pieces.**
  * Side faces split by crossing ribs are merged per plane pair only when
    the pieces lie within a rib thickness of each other. Gussets either side
    of a boss stay separate.
  * The base is the unowned face with the longest root. A boss face is what
    a gusset joins, not its base.
* **Floor-based pockets.** A floor closed all round by concave walls is a
  pocket even when its opening is not one face's inner loop: a keyway in a
  shaft, or a pocket across a seam.
* **Free-form blends.** A B-spline face with one constant principal
  curvature, smooth to two supports, is a blend. This covers rolling-ball
  fillets and tori after NURBS conversion. B-spline edges are fitted to
  lines and circles.
* **Holes.**
  * Pieces of one hole interrupted by another hole are merged: they are
    coaxial, of equal radius, and the axis between them is empty
    (cross-drilled holes).
  * Rims are found by reach, so inclined and saddle-shaped entries count.
* **Bosses.**
  * A coaxial stack that is not a boss as a whole is tried in parts, so a
    flange's hub is found.
  * A cylinder standing on a narrow coaxial ring (a groove floor) is not a
    boss.
  * Heights are measured to the base and top planes, past hidden fillets.
* **Hints.** Only a hint naming a single kind can create a feature. The
  first solid of a body is not hinted.

**Integration.** `app/recognition_service.py` is the shape-level service.
It provides options from the preferences, hints from history, and a
per-shape cache. The analyzers are unchanged. Wiring `requires_features`
into AnalysisRunner (section 7) remains the next step and needs your
review.

**Tests.** There are 109 fixture parts (101 at first build) × 4 variants (as built, moved, all
B-spline, STEP round trip), plus unit tests:

* the convexity oracle;
* FaceIndex parity;
* model, cache, options and hints;
* directional annotations;
* the corpus workflow;
* import purity;
* lazy registries;
* history hints on real geometry;
* the bench driven headless.

**Known limits (v1):**

* B-spline blends need a constant radius (variable-radius fillets are not
  blends yet).
* Threads are found only on recognised holes or bosses.
* A rectangular slot closed by flat walls at both ends is reported as a
  pocket.
* Depressions whose walls are not all swept along one direction (free-form
  or drafted-and-filleted cavities) still fall to the residual pass.
* Mid-surface (thin-wall) reasoning à la Lockett & Guenov is not used.
* The bench's toolbar appears after FreeCAD restarts; the command greys
  out at once when the option is turned off.

## 14. Validation on real models (engine 1.1.0)

The first build passed its own fixtures but got many features wrong on
real parts. It was then run on downloaded STEP models, each result drawn
colour-coded next to the ground truth where there was one
(`testing/render.py`), judged by eye, fixed and run again.

**MFCAD** (Cao et al. 2020: 15 machining-feature classes, per-face labels),
400 parts, 9,050 faces, scored per face:

| | before | after |
|---|---|---|
| face accuracy | 0.776 | 0.993 |
| parts fully right | 72 / 400 | 371 / 400 |
| chamfer recall | 0.34 | 0.87 |
| cutout (passage) recall | 0.70 | 0.997 |
| slot recall | 0.45 | 0.98 |
| step recall | 0.45 | 0.99 |
| pocket recall | 1.00 | 1.00 |

What was left: V-slots whose two walls a step cuts apart, and a few
bevels whose supports are themselves bevels.

**Printed parts** (Prusa i3 MK3S, Voron 0) were judged visually. What they
showed was wrong, and the fixtures now added for each:

* *Profile classifier* (`_recognizers/depressions.py`, after steps). The
  unclaimed faces are split into concave components. One whose faces are
  all walls (normals across a sweep direction) or caps (normals along it)
  is classified by its wall profile:
  * a ring with no cap is a cutout, and a ring with one cap is a pocket;
  * a path whose two rims face the same way is a slot (rectangular, V,
    polygonal);
  * a path whose rims face different ways is a step;
  * one or two caps make it blind or closed.

  Coplanar pieces of a wall that a crossing feature split count as one
  wall. Faces must look into the void, which tells a depression from a
  block standing on a plate. Two thin rims mark an L-bracket's inside
  corner, which is not a step. Fixtures: `step_blind_corner`,
  `slot_v_groove`, `step_slanted`, `cutout_hex_crossed`.
* *Stepped pockets.* Walls at several depths along one direction, opening
  on one side only, form one pocket. Examples are a nut trap with bridging
  layers, and a counterbored pocket. When a hole pierces its floor,
  `pierced` is set. Fixture: `pocket_nut_trap_bridged`.
* *Internal voids.* A shell whose faces meet only each other is a `void`,
  a new kind. Prusa parts use 0.5 mm voids to force extra perimeters.
  Fixture: `void_internal`.
* *Slits.* Two close flat walls facing each other, meeting nothing
  concavely, form a through slot (`profile: slit`), such as a clamp's
  split. Fixture: `slot_clamp_slit`.
* *Crossed passages.* A passage cut in two by another passage is joined
  back into one cutout. Fixture: `cutout_crossed_passages`.
* *Chamfers.*
  * A convex bevel may be as broad as twice its smaller support. MFCAD
    cuts block corners off at half the block.
  * A concave strip must be under half its supports, or it is a pocket's
    wall.
  * A concave bevel must stand for a corner of no more than about 100°.
    Wider corners are the sides of hexagonal holes.
  * A convex strip with a long concave edge is the side of a step or
    groove, not a bevel.
  * When two strips bevel each other, the more even-angled one wins.
  * The default largest chamfer width is now 25 mm.
* *Steps.* Two faces whose rims face the same way are a V-groove, so they
  are left to the slot classifier.
* *Ribs.* A "rib" whose top is far broader than a strip of the rib's
  width is the wall between two engraved grooves (embossed text), so it
  is rejected.
* *Bosses.* A shoulder must lie within the boss's footprint. Before this,
  a split boss took the plate it stands in as its shoulder.
* *Order.* Pockets now run before slots, so a pocket's opposite walls are
  not read as a slot. An obround closed region is left to the slot
  recognizer.

## 15. The bench, second version

The developer bench's viewport colouring confused people. The selected
feature was dark yellow and the faces it stands on were light yellow, so
a small fillet could look like the big face beside it. Results that were
right looked wrong.

The bench is now built from the results panel's parts:

* the findings card, toolbar and 28 px tree delegate, with grouping by
  kind, by review or flat, and a filter;
* the row camera buttons, Z / F / Shift+F / Home, and double-click;
* HighlightScene for the viewport.

Colouring now works like this:

* Each face has exactly one colour. The selected feature (or kind, or
  faces in no feature) is drawn in its kind's colour.
* Everything else is a pale tint of its kind's colour, or gray.
* Support faces are listed in the details as links, which show them on
  request, instead of being tinted.

Other changes:

* **Picking.** Clicking a face in the 3D view casts a ray at the
  recognised shape and selects the face's feature. The face is numbered
  by FaceIndex, so there is no translation step. Ctrl+click picks the
  faces of a missed feature.
* **Reviewing.** Reviews are done in the tree: C, W and U, or right-click
  for "Really a...".

Three sources of nonsense results were removed:

* **Stale shapes.** The bench converted the model once and reused that
  shape on every Recognise, so after an edit it recognised the old shape,
  while history hints were read from the new one. It now re-reads the
  model on every run and re-meshes the view.
* **Hint-made features without geometric support.** A history hint that
  matches no recognised feature now makes one only if the faces can be
  that kind; a hole, for example, needs an inside cylinder or cone. Its
  subtype comes from the geometry: "countersunk" needs a cone. Such
  features carry `from_history`, have lower confidence, and are shown
  as "from history".
* **Unchecked numbering.** After every run, the bench compares each
  FreeCAD `Face<n>` with the engine's face n by area and centre of mass.
  If any differ, the banner turns red.

## 16. A user part: the antenna holder (engine 1.2.0)

The antenna holder is now in the corpus (`tests/recognition/corpus/`)
with tests of its own (`tests/test_corpus.py`). It is a moulded holder
with a round cradle for a rod cut across four walls. Here is what was
wrong and what changed:

* **The cradle was read as a Ø13.4 through hole.** A hole's coverage
  added up its pieces' arcs, so four half-cylinders on four walls made a
  full circle. Coverage is now the union of the arcs around one axis.
  The cradle pieces on the two inner walls are now the ribs' curved tips.
  The pieces on the outer walls form a new `slot` subtype, `saddle`,
  which lists the rib tips as support.
* **The holes were read as countersunk.** Moulded bores are drafted, so
  they are cones, and a cone at the opening was taken as a countersink.
  Cones within `max_bore_taper` (10°, adjustable in preferences) now
  count as bores. The holes are counterbored from both ends (`cb2_*`).
  When both ends match, the hole is entered from the bigger face.
* **The lead-in chamfers were missed.** A narrow inside cone at a hole's
  entry is a chamfer (up to 0.35 × its radius wide); wider is a
  countersink.
* **The chamfers along the cradle's curved edges were missed.** A
  chamfer of any surface is now found when it is a constant-width band
  between two edges at chamfer angles. Small patches joining two chamfers
  smoothly become corners. Chamfer pieces that meet tangentially join one
  chain.
* **The rim top was part of the ribs.** A rib's top that also meets
  other features at convex edges (holes, outer walls) is the surface the
  ribs are flush with. It moves to the rib's support, and `tip` becomes
  `flush`.
* **A pocket was missing a wall.** Ribs run first, so a thin wall
  between a pocket and the next cavity took the pocket's wall. The pocket
  now claims its wall loop back, and the rib keeps that side as support.
  This does not apply to a rib standing inside the pocket.

**Part form.** `result.form` (`form.py`) is the base feature a rebuild
would start from, as FeatureWorks does: extrusion, 2.5D, revolve or
mixed. It is a description of the part, not a feature, so it owns no
faces. The antenna holder is "mixed": 2.5D along +Z for 98% of its area,
with only the cradle's four faces off that direction.

## 17. Moulding and printing details (engine 1.3.0)

These features are the ones a moulder or a printer asks about. They are
the details that decide whether a part can be made, not just its main
shapes. They are found by six new recognizers and five passes that run
after the residuals. Every threshold is a preference, in a new group
called "Moulding details".

**Draft.** Moulded features are rarely square. Every new recognizer
accepts draft:
* Walls within `max_draft` count as upright.
* Beams (snap fits, ribs) within `max_rib_draft`.
* Bores are cones up to `max_bore_taper`.

Each kind has drafted fixtures:

| Feature | Drafted fixture |
|---|---|
| Snap fit | 1–1.5° per side, so the beam thins toward the hook. Thickness is measured at the root; `thickness_tip` is measured by a ray just under the catch. |
| Crush ribs | Inside a cone |
| Vents | 1° slots, and a grille of cone holes |
| Lips | 1° |
| Rim grooves | 2° |
| Text | 2°, engraved and embossed |
| Pins | 0.7° |
| Bosses tied to a wall | Drafted |

**Recognizer order.** The order matters because faces are claimed:
1. **Ring grooves** run before holes. Otherwise an O-ring groove's round wall reads as a hole.
2. **Crush ribs** run right after holes. A pocket would otherwise take their faces. The hole then claims the floor between the ribs.
3. **Snap fits and lips** run before ribs. A snap beam is a thin wall too.

**Recognizers**

| Recognizer | What it looks for | How it avoids false hits |
|---|---|---|
| `snap_fits` | A catch face looking back at the base. Under it, a beam face (B) rising from the base in a valley. A back face (A) at most `max_snap_thickness` behind B. A hook: the faces above the catch, at most 8. | The hook must be a free end: it touches only the beam. The beam spans most of its length and is at least 2t long. It is ≥ 0.3 mm thick and at least as wide as it is thick. This rejects housing walls, double hinges and PCB silkscreen. |
| `lips` | A ring top whose walls drop to a rim face P, which is itself a ring. The other side keeps going down. | Height up to `max_lip_height`. Length at least 15× the thickness, so a collar round a hex nut seat is not a lip. |
| `ring_grooves` | A ring floor walled on both loops. | Width is constant (max/min ≤ 1.3) and small against the ring's length. This rejects a pocket round an island. |

**Post passes**

| Pass | Turns what into what | Rule |
|---|---|---|
| `living_hinges` | Grooves into hinges | A ray from the groove's bottom finds a web ≤ `max_hinge_thickness`. The groove is at least twice as deep as the web, and at least twice as long as it is wide. |
| `snap_windows` | Sets `mould_window` on a snap fit | An opening sits under its catch. |
| `nut_traps` | A hexagonal pocket on a hole's axis into a `nut_trap` | — |
| `nut_traps` | A pocket or slot across a bolt hole into a `nut_slot` | It must be nut-sized: 1.4–2.3× the bolt, thinner than wide, a known ISO nut. |
| `vents` | Through openings into a vent | Same shape and wall, an even pitch, and dense enough. Non-elongated openings need at least 6, so ordinary hole patterns stay holes. |
| `text` | Small shallow shapes on one face into text | At least `min_text_glyphs` of them, mostly with thin strokes (stroke ≤ 0.25 × glyph size). The text absorbs any feature lying among its letters. |

**Holes and voids**
* **Teardrop holes.** Two tangent planes meeting at a point make a hole with `profile: teardrop`.
* **Vented voids.** A cavity whose only ways out are holes is a vented void. It lists its escape holes.

**Printing (directional).** `annotate_directional` with a print direction adds two features:
* **Overhangs:** down-facing faces beyond `fdm_overhang_angle`, off the bed. They are `flat` or `sloped`.
* **Bridges:** down-facing flats held up by walls on two sides. They carry `span` and `too_long` (over `fdm_max_bridge`).

It also marks `bed_edge_relief` on chamfers and fillets that touch the bed.

**Validation**

| Check | Result |
|---|---|
| Fixtures | 553 of 553 cases pass. Each new fixture runs in 4 variants: base, moved, nurbs and step. |
| MFCAD | 99.1% of faces, 368 of 400 parts fully right, unchanged. |
| Prusa i3 parts | "PRUSA" on the PSU cover is found as text. The M3 nut slot on the fan shroud is found, with its bolt hole. The hex nut seat on the extruder cover is an M3 nut trap. |
| Voron parts | The engraved hotend names ("REVO" and others) are found as text. |

Three measures, added after a slow real assembly, keep the new passes cheap:
* the cached face levels;
* an inlined `extent`;
* a capped hook search.

On a 3,700-face assembly with an embedded PCB, the snap-fit search dropped from 117 s to under 4 s.
