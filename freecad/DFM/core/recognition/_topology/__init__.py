# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Topology and geometry descriptors (internal).

``build_topology(shape, options)`` numbers faces and edges exactly like
``core.utils.geometry.FaceIndex`` / ``EdgeIndex`` and describes each face
(surface kind, axis, radius, inside/outside, loops, span) and each edge
(the faces either side and the angle they meet at).
"""

from .descriptors import FaceInfo, EdgeInfo, Topology, build_topology  # noqa: F401
