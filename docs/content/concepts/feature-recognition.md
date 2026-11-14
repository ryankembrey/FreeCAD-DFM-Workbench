# Feature recognition

Many rules are about features, not faces: a rib is too thick, a boss stands on its own, a hole is
too deep. The workbench finds these features itself, from the shape of your part, with its
**feature recognition** engine.

## What it finds

Holes, bosses, ribs and gussets, slots and steps, pockets and cutouts, internal voids, fillets and
chamfers, snap fits, living hinges, vents, lips and text. For each, it measures what the rules need:
a hole's diameter and depth, a rib's thickness at its root and its height, a boss's outer diameter
and wall. The full list is in [Recognized features](../reference/features.md).

With a pull or print direction it also works out each feature's draft, which features are undercut
and what overhangs or bridges when printed.

## How it works

The engine reads your part's faces and how they meet: which edges are convex and which concave,
which faces are cylinders, planes or fillets. Known patterns of faces are matched as features: a
cylinder standing on a face is a boss, a pair of parallel faces standing up from a wall is a rib.
Each face belongs to at most one feature.

It needs no model history, so it works on imported STEP files. When your part is a PartDesign Body,
its features (Hole, Pocket, Pad...) are used as hints: they confirm what the engine finds, and add
what the shape alone can't say, such as a cosmetic thread.

## When it gets it wrong

Recognition is heuristic: an unusual shape can be missed, or taken for the wrong kind of feature.
Then a feature rule may miss a problem or report one that isn't there. You can
[ignore a finding](../getting-started/reading-results.md#ignoring-findings) that doesn't apply.

The engine's thresholds (how thin a rib can be, how tall a boss) are all in
**Edit → Preferences → DFM → Feature Recognition**: see [Preferences](../reference/preferences.md).
