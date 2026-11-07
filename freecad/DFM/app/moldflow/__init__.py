# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Mold flow: a fill analysis for injection molding (in the spirit of
Moldflow Adviser), separate from the DFM rule checks.

* mesh.py: the part's surface mesh as a dual domain (both skins of each wall,
  with the wall's thickness and the skins paired across it);
* solver.py: generalized Hele-Shaw flow, filled step by step (CV/FEM), with a
  shear-thinning melt (Cross-WLF) and a frozen layer growing on the mold walls;
* results.py: weld and meld lines, air traps, confidence of fill;
* gating.py: where to put a single gate;
* material.py: the melts (generic grades and one of the user's own);
* analysis.py: one analysis start to finish, and its results as plain data.

Needs numpy and scipy (and gmsh, for the mesh, as the contour tools do).
"""
