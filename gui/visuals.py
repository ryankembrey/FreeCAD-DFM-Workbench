from pivy import coin
import FreeCADGui as Gui  # type: ignore


class DirectionIndicator:
    def __init__(self):
        self.view_node = None
        self.view_trans = None

    def show(self, base_pnt, direction):
        """Creates an arrow at the base point pointing in the specified direction."""
        active_doc = Gui.ActiveDocument
        if not active_doc or not hasattr(active_doc, "ActiveView"):
            return

        view = active_doc.ActiveView
        if not view:
            return

        if self.view_node is None:
            self.view_node = coin.SoSeparator()

            lm = coin.SoLightModel()
            lm.model.setValue(coin.SoLightModel.BASE_COLOR)
            self.view_node.addChild(lm)

            db = coin.SoDepthBuffer()
            db.test.setValue(False)
            self.view_node.addChild(db)

            mat = coin.SoMaterial()
            mat.diffuseColor = (1.0, 0.0, 0.0)
            mat.ambientColor = (1.0, 0.0, 0.0)
            mat.specularColor = (0.0, 0.0, 0.0)
            mat.shininess = 0.0
            self.view_node.addChild(mat)

            self.view_trans = coin.SoTransform()
            self.view_node.addChild(self.view_trans)

            arrow_group = coin.SoSeparator()
            cyl_height, cyl_radius = 20.0, 1.0
            cone_height, cone_radius = 8.0, 3.0

            cyl = coin.SoCylinder()
            cyl.height.setValue(cyl_height)
            cyl.radius.setValue(cyl_radius)
            arrow_group.addChild(cyl)

            c_trans = coin.SoTransform()
            cone_y_offset = (cyl_height * 0.5) + (cone_height * 0.5)
            c_trans.translation.setValue(0, cone_y_offset, 0)

            cone = coin.SoCone()
            cone.height.setValue(cone_height)
            cone.bottomRadius.setValue(cone_radius)

            arrow_group.addChild(c_trans)
            arrow_group.addChild(cone)
            self.view_node.addChild(arrow_group)

            if hasattr(view, "getSceneGraph"):
                view.getSceneGraph().addChild(self.view_node)  # type: ignore

        if self.view_trans is not None:
            total_height = 28.0
            pos_offset = total_height * 0.5
            new_pos = base_pnt.add(direction.multiply(pos_offset))
            self.view_trans.translation.setValue(new_pos.x, new_pos.y, new_pos.z)

            rot = coin.SbRotation(
                coin.SbVec3f(0, 1, 0), coin.SbVec3f(direction.x, direction.y, direction.z)
            )
            self.view_trans.rotation.setValue(rot.getValue())

        self._refresh_view(view)

    def remove(self):
        if self.view_node:
            active_doc = Gui.ActiveDocument
            if active_doc and hasattr(active_doc, "ActiveView"):
                view = active_doc.ActiveView
                if view and hasattr(view, "getSceneGraph"):
                    view.getSceneGraph().removeChild(self.view_node)  # type: ignore
            self.view_node = None
            self.view_trans = None

    def _refresh_view(self, view):
        """Triggers a redraw of the 3D view."""
        try:
            viewer = getattr(view, "getViewer", lambda: None)()
            if viewer and hasattr(viewer, "update"):
                viewer.update()
                return

            updater = getattr(view, "update", None)
            if updater:
                updater()
        except Exception:
            pass
