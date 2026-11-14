# DFM Workbench: codebase notes (for AI agents, not the website)

Written 2026-11-14 against `main` at `01959d8` (v0.4.1). These are my working notes from
reading the whole repository, for future sessions working on the workbench or its docs.
Nothing here is published: `docs/_notes/` must stay excluded from the site build.

If something below disagrees with the code, the code wins: fix the note.

---

## 1. What it is

A FreeCAD 1.1+ Python workbench (`freecad/DFM`, ~64k lines incl. a 2.2k-line Qt resource
file) that checks a solid against Design-for-Manufacturing rules for a chosen process and
material, and shows findings on the model. LGPL-2.1. Author: Ryan Kembrey. Repo:
`ryankembrey/FreeCAD-DFM-Workbench`. Docs URL in `package.xml`:
`https://ryankembrey.github.io/FreeCAD-DFM-Workbench/`.

Philosophy (README): power for hobbyists and small businesses; tools that *teach* as well as
critique; everything customisable (Process Library for limits and messages, Preferences for
analyzer parameters).

Python deps (`package.xml`): `cadquery-ocp`, `gmsh`, `scipy`, `numpy`. PySide6 (so FreeCAD
1.1+; 1.0 untested; 0.x unsupported).

## 2. Layout

```
freecad/DFM/
  init_gui.py        DFMWorkbench: toolbar "DFM Analysis" + menu "DFM"; 6 preference pages;
                     installs shape observer at import. Dev toolbar only if bench enabled.
  core/              Pure-ish logic. Imports FreeCAD only in a few places (process.py,
                     threshold_resolvers.py) -> tests stub FreeCAD.
    rules.py         Rulebook enum (49 rules) + RuleType metadata (label, shape, unit,
                     comparison, unit_suffix, description, field_labels, per_material,
                     dependency). Criticality enum (CRITICAL/HIGH/MEDIUM/LOW).
    models.py        Severity, CheckResult, GeometryRef, ProcessRequirement.
    processes/       4 built-in process YAMLs + process.py (Process, Material, RuleLimit,
                     RuleFeedback, ProcessArchetype) + threshold_resolvers.py.
    analyzers/       16 analyzers (measure per face/edge). @register_analyzer("ID").
    checks/          checks (judge analyzer data vs thresholds). @register_check(Rulebook.X).
    base/            BaseAnalyzer, BaseCheck, FeatureCheck.
    registries/      analyzer / check registries, ProcessRegistry (singleton).
    recognition/     Feature recognition engine (pure OCP). README.md + DESIGN.md (933 lines,
                     with an academic reference list in section 12).
    inspection/      Symmetry check + compare bodies (OCP only).
    utils/           geometry (FaceIndex/EdgeIndex, 1-based like FreeCAD FaceN),
                     nominal_wall, mold, conversion (freecad_to_ocp), feature geometry.
  app/               FreeCAD-side orchestration.
    analysis_runner.py  AnalysisRunner.run_analysis(process, material, shape, **reqs)
                        -> list[CheckResult]; rerun_checks_only(process, material) reuses
                        analyzer_cache (instant material switch).
    parallel/        Worker pool in a *plain Python* (not FreeCAD), kept warm.
    contour/         Uniform gmsh mesh + per-triangle measures for the 4 contour tools.
    history.py       AnalysisRun / HistoryManager (JSON under user app data).
    document_object.py  "DFM Results" FeaturePython object stored in the .FCStd.
    recognition_service.py, recognition_hints.py (hints from PartDesign history).
    shape_observer.py, run_estimate.py, process_requirements.py, prefs.py.
  gui/
    task_setup.py    DFM Analysis setup task panel (process, material, requirement rows).
                     open_results(...) = clean entry to the results panel (used by capture).
    task_progress.py Progress panel (RunRequest -> runs analysis -> results).
    task_results.py  Results panel: tabs Results / History / Export.
    results/         presenter, findings tree model, toolbar (group/sort/filter),
                     highlight_scene (Coin), viewport_markers, overlay, camera, row actions.
    process_library.py  Process Library window (tree of process > materials, rule cards,
                     list/grid layouts, EditFeedbackDialog, import/export, restore default).
    rule_cards.py    Card widgets per RuleShape.
    rule_information/  _diagrams.py: SVG template per rule (DIAGRAMS dict);
                     _renderer.py: themed_svg(svg, palette) colour substitution.
    contour/         Contour task panel + legend + Coin scene (draft/thickness/undercut/curvature).
    inspection/      Symmetry Check + Compare Bodies panels and scene.
    report/          PDF report: dialog (options + live preview), layout (QPdfWriter),
                     capture.py (deterministic offscreen viewport images), templates
                     (Standard / Compact / Detailed + user templates), logos.
    preferences.py   Pages: General, Results, Analyzers, Contour (+ Reports, Feature
                     Recognition from their own modules). Fields are declarative
                     (IntField, FloatField, BoolField, ComboField, ColorField...).
    recognition_bench/  Developer tool (off by default; Preferences > DFM > General >
                     Developer Options).
    resources/       .ui files (task_setup, task_results, process_library), icons,
                     compiled into DFM_rc.py.
tests/               401 unittest tests; run_tests.py discovers test_*.py.
  dfm_test_support.py   FreeCAD stand-ins + OCP part builders (shelled box, boss, rib...).
  recognition_support/  Scripts run in subprocesses: freecad_stubs.py (FreeCAD, FreeCADGui,
                        pivy, HighlightScene stand-ins), ui_script.py (drives Process Library
                        offscreen), render_diagrams.py (diagram contact sheet), end_to_end...
  recognition/corpus/   Approved recognition cases (BREP + STEP).
resources/examples/  Example STEP files (Holder, LEGO brick, bumper, NIST FTC 08,
                     "lastic enclosure 2.STEP" (sic), examples.FCStd).
docs/                Currently a stub Sphinx autodoc site (furo), deployed by
                     .github/workflows/docs.yaml to gh-pages with force_orphan.
.github/images/      README screenshots (hand-made; will go stale).
```

## 3. Commands (toolbar order)

| Command | MenuText | What |
|---|---|---|
| DFM_SetupAnalysis | DFM Analysis | Setup panel -> progress -> results |
| DFM_ProcessLibrary | Process Library | Edit processes/materials/rules |
| DFM_DraftAnalysis | Draft Analysis | Contour by draft angle (uniform mesh) |
| DFM_ThicknessAnalysis | Thickness Analysis | Contour by wall thickness |
| DFM_UndercutAnalysis | Undercut Analysis | Categorical contour: release direction |
| DFM_CurvatureAnalysis | Curvature Analysis | Contour by tightest radius |
| (separator) | | |
| DFM_SymmetryCheck | Symmetry Check | Plane/axis symmetry, what isn't |
| DFM_CompareBodies | Compare Bodies | What changed between two bodies |
| DFM_RecognitionBench | Feature Recognition Bench | Dev only |
| DFM_ShowNormals | Show Normals | Registered, not on the toolbar |

## 4. Rules, processes, materials (the data the docs must reflect)

* 49 rules in `Rulebook`. Shapes: TARGET_AND_LIMIT ("Aim for" / "At least" or "At most"),
  TARGET_ONLY, LIMIT_ONLY, MIN_AND_MAX, BINARY ("If detected" -> ERROR/WARNING).
  Units: mm, °, %, ×, mm². `unit_suffix` like "of nominal wall".
* "Target" = recommended (warning when crossed); "limit" = hard limit (error).
* Each rule has a diagram (bad left / good right, 340x165 viewBox) and a one-line `brief`.
* Process YAML structure: `name, category, archetype, description, active_rules,
  rule_feedback{RULE: {warning_msg, error_msg}}, rule_criticality{RULE: LEVEL},
  materials{Name: {category, is_active, rule_limits{RULE: {target, limit | binary_severity}}}}`.
  Values are strings. "Default" material holds every rule's thresholds; other materials
  override only what differs. `Process.from_yaml` **silently drops unknown keys**, and the
  registry re-serialises user copies, so do not hide extra metadata inside these files.
* User copies live in `<UserAppData>/dfm/processes/*.yaml` and override built-ins;
  `merge_new_rules` adds rules that were added to a built-in later.
* Feedback messages use placeholders: `{measured} {target} {limit}` plus per-check ones
  (`{feature} {size} {nominal} {reference} {sealed_by}`...). `BaseCheck.message_placeholders`
  is the source of truth; a test enforces it.

| Process (file) | Archetype | Rules | Materials |
|---|---|---|---|
| Plastic Injection Molding (`plastic_injection_molding.yaml`) | inferred INJECTION_MOLDING | 29 | Default, PLA, ABS, Acetal/POM, PMMA, LCP, Long-Fiber Reinforced, PA, PC, PET/PBT, PE, PSU, PP, PS, PUR, PVC |
| FDM (`fdm_3d_printing.yaml`) | FDM | 15 | Default, PLA, PLA-CF, PETG, ABS, ASA, TPU, PA, PA-CF, PC |
| Resin 3D Printing (SLA) (`resin_3d_printing.yaml`) | RESIN | 15 | Default, Standard, Tough/Durable, Flexible/Elastic |
| Powder Bed Fusion (SLS) (`powder_bed_fusion.yaml`) | POWDER_BED | 16 | Default, PA12 (SLS), PA12 (MJF), PA11, PA12 Carbon-filled, TPU |

Archetypes decide required inputs and derived thresholds:
* INJECTION_MOLDING: pull direction (+ optional side actions), nominal wall (auto/manual).
* FDM: print orientation, nozzle diameter, layer height. MIN_WALL_THICKNESS and
  MIN_PRINTED_TEXT_STROKE are *derived from the nozzle* at run time (2 / 1 extrusion widths).
* RESIN: print orientation. POWDER_BED: waives print orientation.

Rule -> check -> analyzer (handy for docs "how it is measured"):
* Draft: DRAFT_ANALYZER. Min wall: RAY_THICKNESS_ANALYZER. Max wall: SPHERE_THICKNESS_ANALYZER.
* Undercut: UNDERCUT_ANALYZER. Sharp corners: SHARP_CORNER_ANALYZER.
* Overhang + support %: OVERHANG_ANALYZER. Bridge: BRIDGE_SPAN_ANALYZER. Bed contact:
  BED_CONTACT_ANALYZER. Cusp: STAIRCASE_ANALYZER.
* Boss/rib/hole/spacing/root radius/hinge/snap fit/text/feature draft: FEATURE_ANALYZER
  (feature recognition + nominal wall).
* Wall variation: THICKNESS_TRANSITION_ANALYZER. Mold steel: MOLD_STEEL_ANALYZER.
* Printed holes/pins/text/sealed cavity/escape holes/cupping: PRINT_FEATURE_ANALYZER.
  Print gap: PRINT_GAP_ANALYZER. Bed edge: BED_EDGE_ANALYZER. Flat face: FLAT_FACE_ANALYZER.
  Part aspect: PART_EXTENT_ANALYZER.

"Five touchpoints" for a rule (enforced by `tests/test_rule_completeness.py`): Rulebook
entry, registered check, analyzer, diagram, YAML (active + feedback + criticality + Default
thresholds). Docs work should add two more: citation entries and docs coverage.

## 5. Citations: current state (important for the citations page)

Sources exist only as **free-text YAML comments** above some threshold entries, plus prose in
process descriptions. Not machine-readable. Coverage measured 2026-11-14 (a threshold entry
counts as sourced if a comment directly precedes it):

| File | Materials | Threshold entries | With a source comment |
|---|---|---|---|
| plastic_injection_molding.yaml | 16 | 70 | 3 (snap-fit strain: Bayer/Covestro) |
| fdm_3d_printing.yaml | 10 | 38 | 5 |
| resin_3d_printing.yaml | 4 | 15 | 15 (Formlabs, Xometry, HLH, Prototek) |
| powder_bed_fusion.yaml | 6 | 20 | 20 (Formlabs Fuse, HP MJF, Xometry, Materialise, HLH...) |

Comment vocabulary already in use: "Ours" = the author's engineering judgement (no published
figure), "derived" values (e.g. through holes get 8/3 of blind), and "placeholder, not a
published value". Keep these distinctions in any structured citation schema.

The injection-molding wall-thickness numbers look like inch conversions (ABS 1.14-3.56 mm =
0.045-0.140 in; Acetal 0.76-3.05 = 0.030-0.120 in; PMMA 0.64-12.70 = 0.025-0.500 in), i.e. a
widely reproduced per-material wall-thickness table. Its origin is not recorded: ask Ryan
before attributing it.

The recognition engine's academic sources are in `core/recognition/DESIGN.md` section 12.

## 6. Running things headless (verified 2026-11-14)

### Tests and offscreen Qt, no FreeCAD

```bash
pip install PySide6 "cadquery-ocp<7.9" numpy scipy pyyaml   # OCP 7.8.1.1.post1
QT_QPA_PLATFORM=offscreen python3 tests/run_tests.py       # 401 tests, ~2 min, all pass
```

* **OCP version matters.** The code uses OCP 7.8's API (`TopoDS.Face_s`, `..._s` statics,
  `TopTools_IndexedMapOfShape`).
  * A plain `pip install cadquery-ocp` today gives 8.0.1 (+ `cadquery-ocp-proxy`), and the
    workbench fails to import (`cannot import name 'TopTools_IndexedMapOfShape'`).
    `package.xml` does not pin it. Possibly a user-facing install risk: tell Ryan.
  * conda-forge `ocp` 7.9.3 drops the `_s` suffix (`TopoDS.Face`), so it fails too.
* Any Qt panel can be driven offscreen with `tests/recognition_support/freecad_stubs.py`
  (import it first, in a fresh interpreter) and `widget.grab().save(png)`. Pattern:
  `ui_script.py`. Process Library: `PL.ProcessLibrary()`, `resize`, `show`,
  `lib.tree_view.select_material(process, material)`, `processEvents()`, `grab()`.
  Needs `Gui.getMainWindow` and `Gui.PySideUic.loadUi` shims (see ui_script.py).
* Analysis without FreeCAD: `AnalysisRunner().run_analysis(...)` on an OCP shape with
  `analysis_runner.freecad_to_ocp = lambda s: s` (see end_to_end_script.py). Required
  kwargs per process: PIM `PULL_DIRECTION=gp_Dir(...)`, `SIDE_ACTIONS=[]`,
  `NOMINAL_WALL_THICKNESS={"mode": "auto"}`; FDM `PRINT_ORIENTATION, NOZZLE_DIAMETER,
  LAYER_HEIGHT`; resin `PRINT_ORIENTATION`; powder bed nothing.
* Rule diagrams need no FreeCAD at all: `DIAGRAMS[rule].svg` (bytes with tokens `__STROKE__ __BODY__
  __ACCENT__ __BAD__ __GOOD__`) + `themed_svg(svg, QPalette)`. `render_diagrams.py` makes a
  light/dark contact sheet.

### The docs environment (pixi.toml, added 2026-11-14)

* conda-forge FreeCAD 1.1 (OCCT 7.9.3) + PyPI `cadquery-ocp-novtk==7.8.1.1.post1`: the no-VTK
  OCP build avoids the VTK side-load hack described below. Needs `libc = "2.31"` in
  `[system-requirements]` (the wheel is manylinux_2_31).
* conda-forge's FreeCAD ships a *regular* `freecad` package (with `__init__.py`), which hides the
  repo's `freecad/DFM` namespace. `docs/_pipeline/env.py` installs its own `freecad` namespace
  module in `sys.modules`. `tests/run_tests.py` would hit the same problem in that environment,
  which is why there is no `pixi run test`.
* FreeCAD's `import FreeCAD` isn't on the env Python's path (the 3D tier runs the `freecad`
  binary instead). FreeCAD with `QT_QPA_PLATFORM=offscreen` fails ("No valid GL context"):
  the 3D tier needs Xvfb from the OS.
* The sandbox can't reach `conda-mapping.prefix.dev`, so `pixi lock` fails there; test installs
  used `conda-pypi-map = false` in a throwaway copy. Ryan's machine and CI write the real lock.
* `docs/build.py` output was byte-identical under the pixi env (PySide6 6.11, OCP novtk) and
  plain pip (PySide6 6.12, OCP 7.8.1): ~1 s per run, 159 files.

### The screenshot engine (docs/_pipeline/shots, added 2026-11-14)

All shots (panels too) run inside real FreeCAD under Xvfb so they carry FreeCAD's stylesheet.
Lessons, each paid for:

* `FREECAD_USER_HOME=<tmp>` isolates FreeCAD completely (user data and config both land there).
* Don't close the Start page from a script (`QMdiArea.closeAllSubWindows()`): its timer then
  segfaults FreeCAD (StartGui.so). Leave it open.
* FreeCAD 1.1 starts the Tasks dock in **overlay mode** (`OverlayRight`, ~378 px, laid over
  the 3D view): the task panel gets cut off at the window edge and covers the part. Fix (Ryan's
  idea): take it out of the overlay like a user would: cursor over the dock
  (`QCursor.setPos`) + `Gui.runCommand("Std_DockOverlayToggle")` (F3; it acts on the dock under
  the cursor, so without the cursor it toggles the wrong thing). Fallback
  `Std_DockOverlayAll`. Then `resizeDocks([dock],[500])` and plain `fitAll()` frame the part.
  `Std_DockOverlayToggleRight` did nothing. Overlay state is stored in
  `BaseApp/MainWindow/DockWindows/OverlayRight` (`Widgets = "Tasks,"`).
* Fonts: a private fontconfig (only `$CONDA_PREFIX/fonts`) + `QApplication.setFont(Ubuntu 10)`.
  Without the explicit app font, Qt picked a monospace fallback.
* Offscreen `saveImage` doesn't recompute clipping (see report/capture.py); window grabs of the
  live view are fine.
* Anything time-based in a shot changes every retake: pin it (history shot patches
  `app.history.datetime`). Paths too: the General prefs page prints the worker Python's path.
* Drive tools via their panels: `ContourTaskPanel(measure, title)` + `_apply_object(obj)` +
  `_on_generate()`; range via `sb_range_lo/hi`; `SymmetryPanel`/`ComparePanel` +
  `_apply_*_from_selection` + `run()`; `TaskSetup()`; results via `AnalysisRunner` +
  `task_setup.open_results`; Save = `presenter.handle_save()`; tabs = `view.form.tabWidget`.
* All 25 shots take ~5 min; a retake with no real change rewrites nothing (rendering is
  deterministic under Xvfb + llvmpipe) once gmsh meshes on one thread (`OMP_NUM_THREADS=1`):
  its parallel meshing changed ~100 pixels of the contour shots on every run.
* The noise guard counts a picture as changed past 50 pixels differing by more than 40: a first
  try at "0.2% of pixels" silently kept an image whose timestamp text had changed.
* Bugs the shots surfaced (not fixed, told Ryan): the History trend chart's y labels read
  "190, 667, 333, 0"; its Compare said 0 resolved though errors fell between runs; the results
  header truncates the model name to "E…".

### Real FreeCAD GUI under Xvfb (for 3D viewport shots)

What worked in the cloud sandbox (GitHub release downloads were blocked; conda-forge worked):

1. Solve and install with py-rattler (pixi's engine): `freecad=1.1, python=3.12, gmsh,
   python-gmsh, scipy, pip` from conda-forge -> FreeCAD 1.1.3, OCCT 7.9.3.
2. `pip install --no-deps cadquery-ocp==7.8.1.1.post1`, and `pip install --no-deps --target
   <dir> vtk==9.3.1`, then `LD_LIBRARY_PATH=<dir>/vtkmodules` (the OCP 7.8 wheel links
   `libvtkWrappingPythonCore3.12-9.3.so`). Two OCCTs in one process worked fine.
3. `xvfb-run -a -s "-screen 0 1920x1200x24" env/bin/freecad script.py`. In the script:
   `import freecad; freecad.__path__.append(REPO + "/freecad")` (the namespace package is
   already imported), `import freecad.DFM.init_gui`, `Gui.activateWorkbench("DFMWorkbench")`,
   do work inside `QtCore.QTimer.singleShot(...)`, then `os._exit(0)`.
   `print()` goes to the Report view, not the terminal; write files or use stderr.
4. Scripted analysis -> results panel: `AnalysisRunner().run_analysis(...)` on `obj.Shape`, then
   `gui.task_setup.open_results(obj, process, process_name, material, results, runner, kwargs)`.
   Holder.stp with PIM/ABS: 464 findings, whole run (launch + analysis + capture) ~78 s.
5. Captures: `Gui.ActiveDocument.ActiveView.saveImage(path, w, h, "White")` for the viewport,
   `Gui.getMainWindow().grab()` for the window (includes the task panel).
   `gui/report/capture.py` already has deterministic camera setup (writes camera node directly,
   sets clipping, hides nav cube): reuse it for docs shots.
6. Gotchas seen: default user config floats the task panel over the viewport (needs a pinned
   FreeCAD user profile for docs), Start page tab is open, Holder.stp is a machined part so
   PIM paints it nearly all red (use purpose-built demo parts).

## 7. Feature areas a user guide must cover

* **Setup panel**: pick body (auto-selects), process, material; requirement rows (pull
  direction with pick/flip, side actions list, print direction, nozzle, layer height, nominal
  wall auto/manual with "measure now"); Run.
* **Progress panel**: per-rule rows, overall bar, time left (run_estimate), abort.
* **Results panel**: verdict header (Failed/Passed, counts), Model/Process/Material row
  (material combo switches instantly via rerun_checks_only), tabs:
  * Results: findings tree grouped by criticality (Critical/High/Medium/Low/Passed), E/W/I
    filters, markers toggle, group/sort; camera buttons on rows; highlight on model.
  * History: compare two runs (regressed/fixed), trend chart.
  * Export: PDF report (presets/templates, live preview) and CSV.
  * Results persist as a "DFM Results" object in the document.
* **Process Library**: tree (category > process > materials with override-count badges),
  rule cards (enable, criticality, aim-for/limit fields, units, feedback editor with
  placeholder chips), list/grid layouts (grid shows diagrams), filter/search, add/remove
  process and material, import/export YAML, undo/redo, restore default, save.
* **Contour tools**: draft, thickness, undercut (categorical legend: release directions, side
  actions, visit regions), curvature (default colormap Viridis for curvature; global default
  "Cool-Warm"; colormaps viridis/cividis/plasma/inferno/magma/coolwarm/RdBu/BrBG/turbo).
* **Inspection**: Symmetry Check (plane/axis, asymmetry map, named differences), Compare
  Bodies (A reference vs B, deviation map, "Boss Ø10" style names); saved in document tree.
* **Preferences > DFM**: General (parallel processing, developer options), Results, Reports,
  Analyzers (per-analyzer panels: sphere/ray thickness, bridge, draft, overhang, undercut,
  sharp corners), Contour, Feature Recognition (every RecognitionOptions field).
* **Feature recognition**: kinds hole, boss, rib, slot, step, pocket, cutout, void, fillet,
  chamfer, protrusion/depression, snap_fit, living_hinge, vent, lip, text (table with
  parameters in core/recognition/README.md).

## 8. Style conventions worth matching in docs

* Sentence case labels (FreeCAD handbook). British-leaning prose in messages ("mould" appears
  alongside "mold"; UI uses "Molding"). Short, plain, explanatory sentences; messages say
  why it matters and how to fix it.
* Colours: accent `#5b9bd5`, bad `#d9534f`, good `#4caf50`; progress done `#3a9a4a`,
  found `#d08a1e`, running `#2f7fd0`.
* SPDX header on every source file: `LGPL-2.1-or-later`, `Ryan Kembrey`, "Part of the DFM addon."

## 9. Owner preferences

* Never open pull requests on Ryan's repos: hand over files; he applies changes locally.
