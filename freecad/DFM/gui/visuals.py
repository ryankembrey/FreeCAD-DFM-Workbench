# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

import math

from pivy import coin
from PySide6 import QtCore, QtGui

import FreeCADGui as Gui  # type: ignore


LABEL_FONT_POINT_SIZE = 9.5
LABEL_PADDING_X = 7.0
LABEL_PADDING_Y = 3.0
LABEL_CORNER_RADIUS = 4.0
LABEL_MARGIN = 2.0

LABEL_GAP_PX = 7.0

ARROW_HEIGHT = 28.0


class DirectionIndicator:
    def __init__(self, color=(1.0, 0.0, 0.0), label=""):
        self.color = color
        self.label = label
        self.view_node = None
        self.view_trans = None
        self.scale_node = None

        self.label_trans = None
        self.label_image = None
        self.label_size = (0.0, 0.0)

        self.camera_sensor = None
        self.active_camera = None
        self.base_pnt = None
        self.direction = None

        self.current_scale = 1.0
        self.world_per_px = 0.0
        self.viewport_height_px = 0

    def show(self, base_pnt, direction):
        """Creates a constant-screen-size arrow at the base point pointing in the specified direction."""
        active_doc = Gui.ActiveDocument
        if not active_doc or not hasattr(active_doc, "ActiveView"):
            return

        view = active_doc.ActiveView
        if not view:
            return

        self.base_pnt = base_pnt
        self.direction = self._normalised(direction.x, direction.y, direction.z)
        self.viewport_height_px = self._viewport_height(view)

        if self.view_node is None:
            self.view_node = coin.SoSeparator()

            lm = coin.SoLightModel()
            lm.model.setValue(coin.SoLightModel.BASE_COLOR)
            self.view_node.addChild(lm)

            db = coin.SoDepthBuffer()
            db.test.setValue(False)
            self.view_node.addChild(db)

            arrow_sep = coin.SoSeparator()

            mat = coin.SoMaterial()
            mat.diffuseColor = self.color
            mat.ambientColor = self.color
            mat.specularColor = (0.0, 0.0, 0.0)
            mat.shininess = 0.0
            arrow_sep.addChild(mat)

            self.view_trans = coin.SoTransform()
            arrow_sep.addChild(self.view_trans)

            self.scale_node = coin.SoScale()
            arrow_sep.addChild(self.scale_node)

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
            arrow_sep.addChild(arrow_group)

            self.view_node.addChild(arrow_sep)

            if self.label:
                self.view_node.addChild(self._build_label_node())

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

    def _build_label_node(self):
        """Builds the label badge, matching the style of FreeCAD's transform handles.

        The badge is painted into a QImage and displayed through an SoImage so it
        renders at a fixed pixel size, always faces the camera, and reads clearly
        over any model colour. Falls back to plain SoText2 if the image cannot be
        built (e.g. a pivy build that will not accept a Python buffer).
        """
        label_sep = coin.SoSeparator()

        self.label_trans = coin.SoTransform()
        label_sep.addChild(self.label_trans)

        image_node = self._build_label_image()
        if image_node is not None:
            self.label_image = image_node
            label_sep.addChild(image_node)
            return label_sep

        label_sep.addChild(self._build_label_text())
        return label_sep

    def _build_label_image(self):
        """Returns an SoImage holding the painted badge, or None if unavailable."""
        try:
            dpr = self._device_pixel_ratio()
            image = self._paint_badge(self.label, dpr)

            logical_w = image.width() / dpr
            logical_h = image.height() / dpr

            # Coin expects the bottom row of pixels first.
            flipped = image.mirrored(False, True).convertToFormat(
                QtGui.QImage.Format.Format_RGBA8888
            )
            buffer = bytes(flipped.constBits())[: flipped.sizeInBytes()]

            image_node = coin.SoImage()
            image_node.image.setValue(coin.SbVec2s(flipped.width(), flipped.height()), 4, buffer)
            image_node.width.setValue(int(round(logical_w)))
            image_node.height.setValue(int(round(logical_h)))

            image_node.vertAlignment = coin.SoImage.HALF
            image_node.horAlignment = coin.SoImage.CENTER

            self.label_size = (logical_w, logical_h)
            return image_node

        except Exception:
            return None

    def _paint_badge(self, text: str, dpr: float) -> QtGui.QImage:
        """Paints a rounded, filled badge with bold centred text."""
        font = QtGui.QFont()
        font.setPointSizeF(LABEL_FONT_POINT_SIZE)
        font.setBold(True)

        metrics = QtGui.QFontMetricsF(font)
        badge_w = metrics.horizontalAdvance(text) + (LABEL_PADDING_X * 2.0)
        badge_h = metrics.height() + (LABEL_PADDING_Y * 2.0)

        img_w = badge_w + (LABEL_MARGIN * 2.0)
        img_h = badge_h + (LABEL_MARGIN * 2.0)

        image = QtGui.QImage(
            int(math.ceil(img_w * dpr)),
            int(math.ceil(img_h * dpr)),
            QtGui.QImage.Format.Format_RGBA8888,
        )
        image.setDevicePixelRatio(dpr)
        image.fill(QtCore.Qt.GlobalColor.transparent)

        painter = QtGui.QPainter(image)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)

        rect = QtCore.QRectF(LABEL_MARGIN, LABEL_MARGIN, badge_w, badge_h)

        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(0, 0, 0, 70))
        painter.drawRoundedRect(rect.translated(0.0, 1.0), LABEL_CORNER_RADIUS, LABEL_CORNER_RADIUS)

        painter.setBrush(QtGui.QColor.fromRgbF(*self.color))
        painter.drawRoundedRect(rect, LABEL_CORNER_RADIUS, LABEL_CORNER_RADIUS)

        painter.setPen(self._text_colour())
        painter.setFont(font)
        painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, text)
        painter.end()

        return image

    def _text_colour(self) -> QtGui.QColor:
        """White text, unless the badge fill is light enough to need dark text."""
        r, g, b = self.color
        luminance = (0.2126 * r) + (0.7152 * g) + (0.0722 * b)
        return QtGui.QColor(25, 25, 25) if luminance > 0.6 else QtGui.QColor(255, 255, 255)

    def _device_pixel_ratio(self) -> float:
        try:
            screen = QtGui.QGuiApplication.primaryScreen()
            if screen:
                return max(1.0, float(screen.devicePixelRatio()))
        except Exception:
            pass
        return 1.0

    def _build_label_text(self):
        """Plain text label, used only when the badge image is unavailable."""
        text_sep = coin.SoSeparator()

        font = coin.SoFont()
        font.name.setValue("Arial:Bold")
        font.size.setValue(15.0)
        text_sep.addChild(font)

        text = coin.SoText2()
        text.string.setValue(self.label)
        text.justification.setValue(coin.SoText2.CENTER)
        text_sep.addChild(text)

        self.label_size = (60.0, 18.0)
        return text_sep

    def _update_label_position(self):
        """Places the badge just past the arrow tip, offset in screen space.

        The offset direction is the arrow direction projected onto the camera
        plane, so the badge trails the arrow head whichever way it points and
        never lands on top of the shaft. When the arrow points at or away from
        the camera that projection collapses, so the badge goes above instead.
        """
        if self.label_trans is None or self.base_pnt is None or self.direction is None:
            return

        tip = self._add(
            self._base_tuple(), self._mul(self.direction, ARROW_HEIGHT * self.current_scale)
        )

        axes = self._camera_axes()
        if axes is None or self.world_per_px <= 0.0:
            self.label_trans.translation.setValue(*tip)
            return

        right, up, _ = axes

        dx = self._dot(self.direction, right)
        dy = self._dot(self.direction, up)
        magnitude = math.hypot(dx, dy)

        badge_w, badge_h = self.label_size

        if magnitude < 1e-6:
            offset_dir = up
            half_extent_px = badge_h * 0.5
        else:
            sx, sy = dx / magnitude, dy / magnitude
            offset_dir = self._normalised(*self._add(self._mul(right, sx), self._mul(up, sy)))
            half_extent_px = (abs(sx) * badge_w * 0.5) + (abs(sy) * badge_h * 0.5)

        offset_world = (LABEL_GAP_PX + half_extent_px) * self.world_per_px
        anchor = self._add(tip, self._mul(offset_dir, offset_world))

        self.label_trans.translation.setValue(*anchor)

    def _camera_axes(self):
        """Returns the camera's right, up and view vectors in world space."""
        if not self.active_camera:
            return None
        try:
            rotation = self.active_camera.orientation.getValue()
            right = rotation.multVec(coin.SbVec3f(1, 0, 0)).getValue()
            up = rotation.multVec(coin.SbVec3f(0, 1, 0)).getValue()
            view_dir = rotation.multVec(coin.SbVec3f(0, 0, -1)).getValue()
            return (tuple(right), tuple(up), tuple(view_dir))
        except Exception:
            return None

    def _viewport_height(self, view) -> int:
        try:
            size = view.getSize()
            if size and len(size) >= 2 and size[1] > 0:
                return int(size[1])
        except Exception:
            pass
        return 0

    def _base_tuple(self):
        return (self.base_pnt.x, self.base_pnt.y, self.base_pnt.z)

    @staticmethod
    def _add(a, b):
        return (a[0] + b[0], a[1] + b[1], a[2] + b[2])

    @staticmethod
    def _mul(a, factor):
        return (a[0] * factor, a[1] * factor, a[2] * factor)

    @staticmethod
    def _dot(a, b):
        return (a[0] * b[0]) + (a[1] * b[1]) + (a[2] * b[2])

    @staticmethod
    def _normalised(x, y, z):
        length = math.sqrt((x * x) + (y * y) + (z * z))
        if length < 1e-12:
            return (0.0, 0.0, 1.0)
        return (x / length, y / length, z / length)

    def _camera_changed(self, userdata, sensor):
        """Callback triggered whenever the camera moves or zooms."""
        self._update_scale()

    def _update_scale(self):
        """Calculates the exact scale factor needed to keep the arrow a constant screen size."""
        if not self.active_camera or not self.scale_node or not self.base_pnt:
            return

        screen_fraction = 0.10  # %
        scale = 1.0
        h = 0.0

        if self.active_camera.isOfType(coin.SoOrthographicCamera.getClassTypeId()):
            h = self.active_camera.height.getValue()
            scale = (h * screen_fraction) / ARROW_HEIGHT

        elif self.active_camera.isOfType(coin.SoPerspectiveCamera.getClassTypeId()):
            cam_pos = self.active_camera.position.getValue()

            dx = cam_pos[0] - self.base_pnt.x
            dy = cam_pos[1] - self.base_pnt.y
            dz = cam_pos[2] - self.base_pnt.z
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)

            angle = self.active_camera.heightAngle.getValue()
            h = 2.0 * dist * math.tan(angle / 2.0)
            scale = (h * screen_fraction) / ARROW_HEIGHT

        self.scale_node.scaleFactor.setValue(scale, scale, scale)

        self.current_scale = scale
        if self.viewport_height_px > 0:
            self.world_per_px = h / float(self.viewport_height_px)

        self._update_label_position()

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
            self.label_trans = None
            self.label_image = None

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
