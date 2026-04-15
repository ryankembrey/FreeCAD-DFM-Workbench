import math

from pivy import coin

import FreeCADGui as Gui  # type: ignore


class DirectionIndicator:
    def __init__(self):
        self.view_node = None
        self.view_trans = None
        self.scale_node = None

        self.camera_sensor = None
        self.active_camera = None
        self.base_pnt = None

    def show(self, base_pnt, direction):
        """Creates a constant-screen-size arrow at the base point pointing in the specified direction."""
        active_doc = Gui.ActiveDocument
        if not active_doc or not hasattr(active_doc, "ActiveView"):
            return

        view = active_doc.ActiveView
        if not view:
            return

        self.base_pnt = base_pnt

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

            self.scale_node = coin.SoScale()
            self.view_node.addChild(self.scale_node)

            arrow_group = coin.SoSeparator()
            cyl_height, cyl_radius = 20.0, 1.0
            cone_height, cone_radius = 8.0, 3.0

            base_trans = coin.SoTransform()
            base_trans.translation.setValue(0, cyl_height * 0.5, 0)
            arrow_group.addChild(base_trans)

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
            self.view_trans.translation.setValue(base_pnt.x, base_pnt.y, base_pnt.z)

            rot = coin.SbRotation(
                coin.SbVec3f(0, 1, 0), coin.SbVec3f(direction.x, direction.y, direction.z)
            )
            self.view_trans.rotation.setValue(rot.getValue())

        camera = view.getCameraNode()
        if camera:
            if self.camera_sensor is None:
                self.camera_sensor = coin.SoNodeSensor(self._camera_changed, None)

            if self.active_camera != camera:
                if self.active_camera is not None:
                    self.camera_sensor.detach()
                self.active_camera = camera
                self.camera_sensor.attach(self.active_camera)

            self._update_scale()

        self._refresh_view(view)

    def _camera_changed(self, userdata, sensor):
        """Callback triggered whenever the camera moves or zooms."""
        self._update_scale()

    def _update_scale(self):
        """Calculates the exact scale factor needed to keep the arrow a constant screen size."""
        if not self.active_camera or not self.scale_node or not self.base_pnt:
            return

        screen_fraction = 0.10  # %
        arrow_base_height = 28.0
        scale = 1.0

        if self.active_camera.isOfType(coin.SoOrthographicCamera.getClassTypeId()):
            h = self.active_camera.height.getValue()
            scale = (h * screen_fraction) / arrow_base_height

        elif self.active_camera.isOfType(coin.SoPerspectiveCamera.getClassTypeId()):
            cam_pos = self.active_camera.position.getValue()

            dx = cam_pos[0] - self.base_pnt.x
            dy = cam_pos[1] - self.base_pnt.y
            dz = cam_pos[2] - self.base_pnt.z
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)

            angle = self.active_camera.heightAngle.getValue()
            h = 2.0 * dist * math.tan(angle / 2.0)
            scale = (h * screen_fraction) / arrow_base_height

        self.scale_node.scaleFactor.setValue(scale, scale, scale)

    def remove(self):
        if self.camera_sensor:
            self.camera_sensor.detach()
            self.camera_sensor = None
        self.active_camera = None

        if self.view_node:
            active_doc = Gui.ActiveDocument
            if active_doc and hasattr(active_doc, "ActiveView"):
                view = active_doc.ActiveView
                if view and hasattr(view, "getSceneGraph"):
                    view.getSceneGraph().removeChild(self.view_node)  # type: ignore
            self.view_node = None
            self.view_trans = None
            self.scale_node = None

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
