# Contours and inspection

These tools look at the whole part at once, rather than rule by rule. They need no process or
material, and they make good pictures for a design review.

## Contours

Each contour tool meshes the part evenly and colors it by one measurement. Pick the part, set what
the tool asks for, and click **Update contour**. **Save** keeps the contour in the document.

The **Display** settings are shared by all four: a **Color scale** or a **Pass/fail** view, the color
map, smooth or banded colors, and the range of values the colors cover. Set the range by hand to
look closely at the values that matter.

### Thickness Analysis

Colors the part by its wall thickness: thin walls at one end of the scale, thick ones at the other.

[![Thickness Analysis: the model colored by wall thickness](../assets/shots/contour-thickness.png)](../assets/shots/contour-thickness.png)

### Draft Analysis

Colors each face by its angle to the pull direction. See
[injection molding](injection-molding.md#draft-and-undercuts-at-a-glance).

[![Draft Analysis: the model colored by draft angle to the pull direction](../assets/shots/contour-draft.png)](../assets/shots/contour-draft.png)

### Undercut Analysis

Colors each face by the way it releases from the mold, with side actions if you add them.

[![Undercut Analysis: each face colored by the direction it releases from the mold](../assets/shots/contour-undercut.png)](../assets/shots/contour-undercut.png)

### Curvature Analysis

Colors the part by its tightest radius of curvature: small fillets and sharp bends stand out. Flat
faces are gray.

[![Curvature Analysis: the model colored by its tightest radius](../assets/shots/contour-curvature.png)](../assets/shots/contour-curvature.png)

## Symmetry Check

Checks whether a part is symmetric about a plane (a mirror) or an axis, and shows what isn't: faces
only on one side, and faces that don't line up with their partner. With **Auto**, it finds the best
plane for you.

[![Symmetry Check: whether the part is symmetric, and what isn't](../assets/shots/symmetry-check.png)](../assets/shots/symmetry-check.png)

## Compare Bodies

Compares two bodies, such as two versions of a part, and shows what was added, removed or changed,
and where. Each change is named ("Hole Ø8 → Ø10", "Text 8 × 6.65 × 1 mm") with its effect on the
volume.

[![Compare Bodies: what changed between two versions of a part, and where](../assets/shots/compare-bodies.png)](../assets/shots/compare-bodies.png)
