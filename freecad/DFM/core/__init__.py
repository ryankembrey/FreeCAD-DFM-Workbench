# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""The DFM core.

Importing this package imports nothing else, so pure-OCP parts of the core
(``core.utils.geometry``, ``core.recognition``) can be used and tested in a
plain Python with no FreeCAD. The analyzers and checks register themselves
the first time a registry is asked for one (see ``core.registries``).
"""
