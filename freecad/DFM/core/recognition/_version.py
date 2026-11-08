# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Engine version. Bump it whenever recognition output can change for the
same shape and options: it is part of every cache key and golden file."""

ENGINE_VERSION = "1.4.0"  # 1.4.0: resin cupping annotations for a print direction
# 1.3.1: a cored slender boss is a screw boss, not a pin
