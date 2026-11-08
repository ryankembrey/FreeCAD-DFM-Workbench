# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Score recognition against MFCAD (Cao et al. 2020).

MFCAD (https://github.com/hducg/MFCAD) is a set of STEP parts with a label
for every face, one of 15 machining features or stock. Each part comes as
``<name>.step`` and ``<name>.face_truth``. Each STEP face's name is an index
into that part's label list.

    python -m freecad.DFM.core.recognition.testing.mfcad DIR [--limit N] [--render OUT --worst K]

This prints per-face accuracy, a confusion matrix, recall by MFCAD class,
and the parts that are fully right. With --render it draws the worst
parts next to their ground truth.

The labels are pickled lists of ints. They are read opcode by opcode and
never unpickled, so a downloaded file cannot run code.
"""

from __future__ import annotations

import argparse
import collections
import glob
import os
import pickletools
import sys
import time

from ..model import FeatureKind as K

#: MFCAD's label order, worked out from one-class parts (the dataset's own
#: visualiser lists them in a different order).
CLASSES = ("chamfer", "triangular_passage", "rectangular_passage", "6sides_passage",
           "triangular_through_slot", "rectangular_through_slot", "rectangular_through_step",
           "2sides_through_step", "slanted_through_step", "triangular_pocket", "rectangular_pocket",
           "6sides_pocket", "rectangular_blind_slot", "triangular_blind_step", "rectangular_blind_step", "stock")
TO_KIND = {0: K.CHAMFER, 1: K.CUTOUT, 2: K.CUTOUT, 3: K.CUTOUT, 4: K.SLOT, 5: K.SLOT, 6: K.STEP, 7: K.STEP,
           8: K.STEP, 9: K.POCKET, 10: K.POCKET, 11: K.POCKET, 12: K.SLOT, 13: K.STEP, 14: K.STEP, 15: None}

_SAFE_OPS = {"PROTO", "EMPTY_LIST", "BINPUT", "LONG_BINPUT", "MEMOIZE", "MARK", "APPENDS", "APPEND", "STOP", "FRAME"}
_INT_OPS = {"BININT", "BININT1", "BININT2", "INT", "LONG1"}


def read_labels(path: str) -> list[int]:
    """A pickled list of ints, read without unpickling."""
    with open(path, "rb") as fh:
        data = fh.read()
    out = []
    for op, arg, _ in pickletools.genops(data):
        if op.name in _INT_OPS:
            out.append(int(arg))
        elif op.name not in _SAFE_OPS:
            raise ValueError(f"{path}: unexpected pickle opcode {op.name}")
    return out


def load(path: str):
    """(shape, {face number: MFCAD class}) for one part."""
    from OCP.STEPControl import STEPControl_Reader
    from ...utils.geometry import FaceIndex

    reader = STEPControl_Reader()
    if reader.ReadFile(path) != 1:
        raise IOError(f"cannot read {path}")
    reader.TransferRoots()
    shape = reader.OneShape()
    tr = reader.WS().TransferReader()
    labels = read_labels(os.path.splitext(path)[0] + ".face_truth")
    fi = FaceIndex(shape)
    truth = {}
    for i in range(1, len(fi) + 1):
        item = tr.EntityFromShapeResult(fi.face_at(i), 1)
        if item is None:
            continue
        try:
            truth[i] = labels[int(item.Name().ToCString())]
        except (ValueError, IndexError):
            pass
    return shape, truth


def evaluate(files, render_dir=None, worst=0, out=sys.stdout):
    from .. import recognize

    conf = collections.Counter()
    by_class = collections.Counter()
    by_class_ok = collections.Counter()
    parts = []
    t0 = time.time()
    for path in files:
        shape, truth = load(path)
        res = recognize(shape)
        pred = {face: f.kind for f in res.features for face in f.faces}
        ok = 0
        for face, lab in truth.items():
            want, got = TO_KIND[lab], pred.get(face)
            conf[(want.value if want else "none", got.value if got else "none")] += 1
            by_class[lab] += 1
            by_class_ok[lab] += want == got
            ok += want == got
        parts.append((ok / max(1, len(truth)), path, res, shape, truth))
    total = sum(conf.values())
    acc = sum(v for (a, b), v in conf.items() if a == b) / max(1, total)
    kinds = sorted({a for a, _ in conf} | {b for _, b in conf})
    print(f"{len(parts)} parts, {total} faces, {time.time() - t0:.1f} s", file=out)
    print(f"face accuracy {acc:.4f}", file=out)
    print("truth \\ found " + " ".join(f"{k[:9]:>10}" for k in kinds) + "   recall", file=out)
    for a in kinds:
        row = [conf[(a, b)] for b in kinds]
        print(f"{a[:12]:>13} " + " ".join(f"{v:>10}" for v in row) + f"   {conf[(a, a)] / max(1, sum(row)):.3f}", file=out)
    print("by MFCAD class:", file=out)
    for c in sorted(by_class):
        print(f"  {CLASSES[c]:<26} {by_class_ok[c] / by_class[c]:.3f}  ({by_class[c]} faces)", file=out)
    parts.sort(key=lambda p: p[0])
    print(f"parts fully right: {sum(1 for p in parts if p[0] == 1.0)}/{len(parts)}", file=out)
    if render_dir and worst:
        import numpy as np
        from . import render as R
        os.makedirs(render_dir, exist_ok=True)
        for score, path, res, shape, truth in parts[:worst]:
            mesh = R.Mesh(shape)
            rgb = np.tile(R._rgb(R.REST), (mesh.face_count + 1, 1))
            for face, lab in truth.items():
                if TO_KIND[lab] is not None:
                    rgb[face] = R._rgb(R.KIND_COLORS[TO_KIND[lab]])
            name = os.path.splitext(os.path.basename(path))[0]
            R.render(shape, res, os.path.join(render_dir, name + ".png"), views=("iso", "iso_back"), mesh=mesh,
                     truth_rgb=rgb, title=f"{name}: face accuracy {score:.2f}")
            print(f"  {score:.2f} {name}", file=out)
    return acc


def main(argv=None):
    ap = argparse.ArgumentParser(description="Score feature recognition on MFCAD parts")
    ap.add_argument("folder", help="folder of MFCAD .step + .face_truth files")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--render", default=None, help="draw the worst parts here")
    ap.add_argument("--worst", type=int, default=10)
    a = ap.parse_args(argv)
    files = sorted(glob.glob(os.path.join(a.folder, "*.step")))
    if a.limit:
        files = files[:a.limit]
    if not files:
        ap.error(f"no .step files in {a.folder}")
    evaluate(files, a.render, a.worst if a.render else 0)


if __name__ == "__main__":
    main()
