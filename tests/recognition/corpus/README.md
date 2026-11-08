# Feature recognition corpus

Real parts with a reviewed ("golden") recognition result. Every suite run
(`python -m freecad.DFM.core.recognition.testing.run --corpus tests/recognition/corpus`,
or the bench's Suite tab with "Include the corpus") recognises each part
again and compares it with its review.

Each case is three files:

* `<name>.brep`: the part, exactly (OpenCASCADE's own format). This is what is tested.
* `<name>.step`: the same part, to open in FreeCAD or any CAD system.
* `<name>.golden.json`: the review. Each feature found is marked
  * `correct`: must be found again (losing it fails the suite: a regression);
  * `wrong`: a known false positive (reported while it is still found);
  * `missed`: a feature the engine should find but doesn't (its faces were
    picked by hand; reported as a known miss until it is found).

A golden's `status` is `approved` only when every feature was reviewed;
`unreviewed` goldens are checked but never fail the suite.

## Adding a case

1. Preferences > DFM > General > Developer Options: tick
   "Feature recognition test bench" and restart FreeCAD.
2. Open the part, start DFM > Developer > Feature Recognition Bench.
3. Inspect: Recognise. Click through the features; untick "Colour by kind"
   to pick faces on the model.
4. Approve: set each verdict, add any missed features from selected faces,
   name the case and Save to corpus.

Faces are stored by number and by signature (surface kind, area, centroid),
so a golden still applies if a new OpenCASCADE numbers faces differently.

The parametric test parts (fixtures.py) are built fresh by every run and
need no files here; `--export DIR` writes them out as BREP + STEP with their
ground truth if you want to open them.
