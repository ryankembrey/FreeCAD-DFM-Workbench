# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Choose a sensible target body when a tool is opened without one picked.

Order of preference:

1. Whatever is selected in the 3D view / tree.
2. The only body in the document.
3. The only *visible* body, if there are several.

Each tool passes its own ``apply_object`` callback, so this module doesn't need to know
how a tool stores its target.
"""

from typing import Callable, Optional

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

# Objects that never make a useful analysis target.
_SKIP_TYPES = {"App::Origin", "App::Line", "App::Plane", "App::DocumentObjectGroup"}
_SKIP_TYPE_PARTS = ("Datum", "Sketch", "Origin", "ShapeBinder")

# Containers whose visibility hides everything inside them.
_CONTAINER_TYPES = ("App::Part", "App::DocumentObjectGroup")


def is_effectively_visible(obj) -> bool:
    """Check if an object is visible, accounting for parent folder visibility."""
    try:
        if not obj.ViewObject.Visibility:
            return False
    except Exception:
        return False
    # Walk up the full container chain (App::Part / folder nesting)
    current = obj
    seen = {id(current)}
    while True:
        parent = None
        for candidate in current.InList:
            if candidate.TypeId in _CONTAINER_TYPES and id(candidate) not in seen:
                parent = candidate
                break
        if parent is None:
            break
        seen.add(id(parent))
        try:
            if not parent.ViewObject.Visibility:
                return False
        except Exception:
            return False
        current = parent
    return True


def find_default_body(doc=None):
    """The one body in ``doc`` (default: the active document) that is the obvious target.

    Prefers ``PartDesign::Body`` objects and falls back to any other solid-bearing object
    if there are no bodies. With several candidates, only a single visible one counts.
    Returns None when there is no clear choice.
    """
    doc = doc or App.ActiveDocument
    if not doc:
        return None

    bodies = []
    fallback = []
    for obj in doc.Objects:
        if not hasattr(obj, "Shape") or obj.Shape.isNull():
            continue
        if not obj.Shape.Faces:
            continue
        type_id = obj.TypeId
        if type_id in _SKIP_TYPES:
            continue
        if any(skip in type_id for skip in _SKIP_TYPE_PARTS):
            continue
        if type_id == "PartDesign::Body":
            bodies.append(obj)
        elif not type_id.startswith("PartDesign::"):
            # App::Part containers, Part WB solids, imported STEPs, etc.
            fallback.append(obj)

    candidates = bodies if bodies else fallback

    if len(candidates) == 1:
        return candidates[0]

    if len(candidates) > 1:
        visible = [obj for obj in candidates if is_effectively_visible(obj)]
        if len(visible) == 1:
            return visible[0]

    return None


def auto_select_body(apply_object: Callable[[object], bool]) -> Optional[object]:
    """Pick a default target and hand it to ``apply_object(obj) -> bool``.

    Returns the object that was applied, or None if nothing was chosen. A selection
    that ``apply_object`` rejects is left in place and the document search still runs.
    """
    selection = Gui.Selection.getSelection()
    if selection and apply_object(selection[0]):
        Gui.Selection.clearSelection()
        App.Console.PrintMessage(f"DFM: auto-selected {selection[0].Label}\n")
        return selection[0]

    obj = find_default_body()
    if obj is not None and apply_object(obj):
        App.Console.PrintMessage(f"DFM: auto-selected {obj.Label}\n")
        return obj
    return None
