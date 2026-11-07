# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Parallel processing for the DFM analysis.

FreeCAD is hard to parallelise with the usual tools, for three reasons:

* ``multiprocessing`` starts workers with ``sys.executable``, which inside
  FreeCAD is FreeCAD itself, so on Windows and macOS every worker would be
  another FreeCAD; and it re-imports the parent's ``__main__``.
* On Linux it forks instead, copying a process that already runs Qt, Coin and
  OpenCASCADE threads. A lock held by one of them at the moment of the fork
  stays locked in the child forever: random hangs and crashes.
* The DFM core uses OCP (cadquery-ocp), which brings its own OpenCASCADE
  build into the same process as FreeCAD's.

So this package runs its own worker processes, started with a plain Python
interpreter (FreeCAD's bundled one, found and test-run once: interpreter.py)
running worker.py, which imports OCP and the DFM core and never FreeCAD. The
shape goes over as a BREP file, faces are shared out in fixed batches, and the
results come back pickled over the worker's stdin/stdout (pool.py, measure.py).

This file imports nothing: worker processes import shapes.py from here without
FreeCAD, so use the modules directly (``from .measure import ...``).

Nothing here can crash FreeCAD: a worker that crashes or hangs is killed and
replaced, and if workers can't be used at all the analysis runs as before, in
FreeCAD's own process.
"""
