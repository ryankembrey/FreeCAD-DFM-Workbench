# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""model_parent(): overlays go beside the model, so saved images include them.

FreeCAD's saveImage() renders only the document's part of the scene. From
FreeCAD 1.2 ``getSceneGraph()`` returns an outer node around it, so an
overlay added there showed on screen but was missing from report pictures.
"""

import sys
import types
import unittest

import dfm_test_support  # noqa: F401  (sys.path)

try:
    from pivy import coin
    import PySide6  # noqa: F401
except ImportError:  # pragma: no cover
    coin = None

if coin is not None and "FreeCADGui" not in sys.modules:
    try:
        import FreeCADGui  # noqa: F401
    except ImportError:
        sys.modules["FreeCADGui"] = types.ModuleType("FreeCADGui")


class _View:
    def __init__(self, root):
        self._root = root

    def getSceneGraph(self):
        return self._root


class _Obj:
    def __init__(self, node):
        self.ViewObject = types.SimpleNamespace(RootNode=node)


@unittest.skipIf(coin is None, "pivy or PySide6 not installed")
class TestModelParent(unittest.TestCase):
    def setUp(self):
        from freecad.DFM.gui.utils.view import model_parent

        self.model_parent = model_parent
        self.model = coin.SoSeparator()
        self.documents = coin.SoSeparator()  # what saveImage renders
        self.documents.addChild(coin.SoSeparator())  # another object
        self.documents.addChild(self.model)

    def test_freecad_1_1_scene(self):
        """getSceneGraph() is the document root itself."""
        parent = self.model_parent(_View(self.documents), _Obj(self.model))
        self.assertTrue(parent == self.documents)

    def test_wrapped_scene_of_newer_freecad(self):
        """getSceneGraph() wraps the document root with the lights."""
        outer = coin.SoSeparator()
        outer.addChild(coin.SoDirectionalLight())
        outer.addChild(self.documents)
        parent = self.model_parent(_View(outer), _Obj(self.model))
        self.assertTrue(parent == self.documents)

    def test_inside_a_container(self):
        """Beside the model inside its container, to take the container's placement."""
        container = coin.SoSeparator()
        container.addChild(coin.SoTransform())
        container.addChild(self.model)
        documents = coin.SoSeparator()
        documents.addChild(container)
        parent = self.model_parent(_View(documents), _Obj(self.model))
        self.assertTrue(parent == container)

    def test_falls_back_to_the_scene(self):
        root = coin.SoSeparator()
        self.assertTrue(self.model_parent(_View(root), _Obj(coin.SoSeparator())) == root)
        self.assertTrue(self.model_parent(_View(root), None) == root)


@unittest.skipIf(coin is None, "pivy or PySide6 not installed")
class TestOverlayHold(unittest.TestCase):
    """An overlay holds the node it is added to: closing a document frees its
    scene, and removing the overlay afterwards must not touch freed memory."""

    def test_add_holds_and_remove_releases(self):
        from freecad.DFM.gui.utils.view import add_overlay, remove_overlay

        parent, node = coin.SoSeparator(), coin.SoSeparator()
        parent.ref()
        before = parent.getRefCount()
        self.assertTrue(add_overlay(parent, node) == parent)
        self.assertEqual(parent.getRefCount(), before + 1)
        self.assertEqual(parent.findChild(node), 0)
        remove_overlay(parent, node)
        self.assertEqual(parent.getRefCount(), before)
        self.assertEqual(parent.getNumChildren(), 0)
        remove_overlay(None, node)  # never attached: nothing to do


if __name__ == "__main__":
    unittest.main()
