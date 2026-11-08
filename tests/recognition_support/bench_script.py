# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The bench's Qt code, driven headless (offscreen Qt, FreeCAD stand-ins):
command gating, Inspect (recognise, select, colour, annotate, pick a face),
Approve (verdicts, a missed feature, save to a corpus), Suite (run, corpus
included), the preferences page and the workbench's developer toolbar.
Run by tests/test_recognition_workbench.py in a fresh interpreter."""

import json
import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import freecad_stubs as stubs  # noqa: E402
from PySide6 import QtWidgets  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
corpus = tempfile.mkdtemp(prefix="bench_corpus_")
stubs.Param.store["RecognitionCorpusDir"] = corpus

try:
    from freecad.DFM.core.recognition.testing import fixtures as FX
    from freecad.DFM.gui.recognition_bench.panel import RecognitionBenchPanel
    from freecad.DFM.gui.recognition_bench.preferences_page import DFMPreferencesRecognition
    from freecad.DFM.gui.tools import recognition_bench  # noqa: F401  (registers the command)
    from freecad.DFM.app.recognition_service import read_options
    from freecad.DFM.gui.recognition_bench import tree as T

    cmd = stubs.gui.commands["DFM_RecognitionBench"]
    stubs.Param.store["DeveloperRecognitionBench"] = False
    assert not cmd.IsActive()
    stubs.Param.store["DeveloperRecognitionBench"] = True
    assert cmd.IsActive()

    class VO:
        Visibility = True

    class Doc:
        Name = "Doc"

    class Obj:
        Label, TypeId, Name = "Housing", "Part::Feature", "Housing"
        Document, ViewObject = Doc(), VO()

    p = RecognitionBenchPanel()
    obj = Obj()
    p.set_target(obj, FX.by_name("housing_shelled").build(), "fixture")
    p.use_hints.setChecked(False)
    p.recognise()
    assert "features" in p.banner.text(), p.banner.text()
    groups = [p.model.index(i, 0).data(T.R_PRIMARY) for i in range(p.model.rowCount())]
    assert groups[:4] == ["Holes", "Bosses", "Ribs", "Pockets"], groups
    counts = [p.model.index(i, 0).data(T.R_COUNT) for i in range(p.model.rowCount())]
    assert counts[:4] == [5, 4, 1, 1], counts

    # select a feature: only it in full colour, the rest pale
    first = p.model.index(0, 0).child(0, 0) if hasattr(p.model.index(0, 0), "child") else \
        p.model.index(0, 0, p.model.index(0, 0))
    first = p.model.index(0, 0, p.model.index(0, 0))
    p.tree.setCurrentIndex(first)
    fid = first.data(T.R_FID)
    feat = p.session.feature(fid)
    assert "Why" in p.details.toHtml() and feat.label in p.details.toPlainText()
    assert stubs.FakeScene.calls[-2][0] == "highlight" and stubs.FakeScene.calls[-2][1] > 0
    from freecad.DFM.gui.recognition_bench.scene import face_colors, kind_color, Focus, Style, pale
    cols = face_colors(p.session.features(), p.session.result.unowned_faces(), Focus.of_feature(fid), Style())
    assert all(cols[f - 1] == kind_color(feat.kind) for f in feat.faces)
    other = next(f for f in p.session.features() if f.id != fid)
    assert cols[other.faces[0] - 1] == pale(kind_color(other.kind))
    cols = face_colors(p.session.features(), [], Focus.of_feature(fid), Style(colour_all=False))
    assert set(cols) == {f - 1 for f in feat.faces}

    # review with keys in the tree
    from PySide6 import QtCore, QtGui
    ev = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, QtCore.Qt.Key.Key_C, QtCore.Qt.KeyboardModifier.NoModifier)
    p._keys.eventFilter(p.tree, ev)
    assert p.session.verdicts[fid] == "correct"
    assert p.tree.currentIndex().data(T.R_FID) != fid       # moved on to the next feature
    p.set_correction(other.id, "step")
    assert p.session.verdicts[other.id] == "wrong"

    # group by review; filter
    act = [a for a in p._group_actions.actions() if a.data() == T.GROUP_BY_REVIEW][0]
    act.setChecked(True)
    p._on_group_changed(act)
    labels = [p.model.index(i, 0).data(T.R_PRIMARY) for i in range(p.model.rowCount())]
    assert labels[:3] == ["Marked wrong", "To review", "Marked correct"], labels
    p.search.setText("hole")
    assert all(p.model.index(i, 0).data(T.R_PRIMARY) != "No feature" for i in range(p.model.rowCount()))
    p.search.clear()

    p.btn_edges.setChecked(True)
    assert stubs.FakeScene.calls[-2][2] > 0          # edges coloured
    p.btn_edges.setChecked(False)
    p.pull.setCurrentText("+Z")
    p.side.setCurrentText("\u2212X")
    p.print_dir.setCurrentText("+Z")
    p.annotate()
    act = [a for a in p._group_actions.actions() if a.data() == T.GROUP_BY_KIND][0]
    act.setChecked(True)
    p._on_group_changed(act)
    assert "Undercuts" in [p.model.index(i, 0).data(T.R_PRIMARY) for i in range(p.model.rowCount())]

    # a click on a face in the 3D view selects its feature; the face numbering checks out
    face = p.session.result.features[0].faces[0]
    p._on_pick(face, False)
    assert p.tree.currentIndex().data(T.R_FID) == p.session.result.features[0].id
    unowned = p.session.result.unowned_faces()
    if unowned:
        p._on_pick(unowned[0], False)
        assert p.tree.currentIndex().data(T.R_FACE) == unowned[0]
    sigs = p.session.result.face_signatures
    good = [(sigs[i].area, sigs[i].centroid) for i in sorted(sigs)]
    assert p.session.check_numbering(good) == []
    swapped = [good[1], good[0]] + good[2:]
    assert p.session.check_numbering(swapped)[:2] == [1, 2]
    p.session.numbering_errors = []
    p._copy_json()
    assert json.loads(QtWidgets.QApplication.clipboard().text())["features"]

    # Review: the rest correct, a missed feature from picked faces, save
    p._rest_correct()
    p._on_pick(1, True)
    p._on_pick(2, True)
    assert p.session.picked == [1, 2]
    p.missed_kind.setCurrentText("step")
    p._add_missed()
    assert p.session.picked == [] and len(p.session.missed) == 1
    p.case_name.setText("housing_bench")
    p._save_golden()
    golden = json.load(open(os.path.join(corpus, "housing_bench.golden.json")))
    assert golden["status"] == "approved", golden["status"]
    assert sum(f["verdict"] == "missed" for f in golden["features"]) == 2   # the step, and "really a step"
    assert os.path.exists(os.path.join(corpus, "housing_bench.brep"))
    assert p.corpus_list.count() == 1

    # Suite, with the corpus
    p.suite_filter.setText("hole_through_simple")
    p.variant_boxes["moved"].setChecked(True)
    p.run_suite()
    rows = sorted(p.suite_rows())
    assert rows == [("hole_through_simple", "base", True), ("hole_through_simple", "moved", True),
                    ("housing_bench", "corpus", True)], rows   # known misses are reported, not failed

    # Preferences page
    page = DFMPreferencesRecognition()
    page.loadSettings()
    page.widgets["max_rib_thickness"].setValue(3.5)
    page.widgets["use_history_hints"].setChecked(False)
    page.saveSettings()
    o = read_options()
    assert o.max_rib_thickness == 3.5 and o.use_history_hints is False

    p.reject()
    assert not stubs.Selection.observers
    print("ok")
finally:
    shutil.rmtree(corpus, ignore_errors=True)
