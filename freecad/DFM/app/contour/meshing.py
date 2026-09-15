# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.


from dataclasses import dataclass
import math
import os
import tempfile

import FreeCAD as App  # type: ignore


RESOLUTION_DIVISORS = {
    "Rough": 15.0,
    "Coarse": 35.0,
    "Fine": 70.0,
    "Ultra Fine": 140.0,
}
DEFAULT_RESOLUTION = "Coarse"

TRIANGLE_WARN = 300_000
TRIANGLE_HARD_CAP = 1_500_000

MIN_ELEMENT_SIZE = 1e-3


@dataclass
class UniformMesh:
    vertices: list
    triangles: list
    cad_normals: list


def element_size_for(shape, resolution: str) -> float:
    diag = shape.BoundBox.DiagonalLength
    divisor = RESOLUTION_DIVISORS.get(resolution, RESOLUTION_DIVISORS[DEFAULT_RESOLUTION])
    return diag / divisor


def estimate_triangle_count(shape, element_size: float):
    """Rough triangle count for a uniform mesh of the given element size.

    Uses ~2.3 triangles per element-area over the surface. Returns None (treated
    as "far too many") for a non-positive size.
    """
    if element_size is None or element_size <= 0.0:
        return None
    try:
        area = shape.Area
    except Exception:
        return None
    return int(2.3 * area / (element_size * element_size))


def _normalize3(x, y, z):
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-12:
        return None
    return (x / length, y / length, z / length)


def _candidate_gmsh_lib_dirs():
    dirs = []
    env = os.environ.get("GMSH_LIB_DIR")
    if env:
        dirs.append(env)
    try:
        import gmsh as _gmsh_probe

        mod_dir = os.path.dirname(os.path.abspath(_gmsh_probe.__file__))
        dirs.append(mod_dir)
        dirs.append(os.path.join(mod_dir, "lib"))
        dirs.append(os.path.join(os.path.dirname(mod_dir), "lib"))
        for entry in os.listdir(mod_dir):
            full = os.path.join(mod_dir, entry)
            if os.path.isdir(full) and entry.lower().startswith("gmsh"):
                dirs.append(full)
                dirs.append(os.path.join(full, "lib"))
    except Exception:
        pass
    for base in list(getattr(__import__("site"), "getsitepackages", lambda: [])() or []):
        dirs.append(base)
        dirs.append(os.path.join(base, "gmsh"))
        dirs.append(os.path.join(base, "lib"))
    dirs.extend(
        [
            "/usr/lib/x86_64-linux-gnu",
            "/usr/lib64",
            "/usr/lib",
            "/usr/local/lib",
        ]
    )
    seen = set()
    out = []
    for d in dirs:
        if d and d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def _find_libgmsh(dirs):
    import re

    pattern = re.compile(r"^libgmsh\.so(\.\d+)*$")
    exact = []
    for d in dirs:
        try:
            entries = os.listdir(d)
        except Exception:
            continue
        for name in entries:
            if pattern.match(name) or name == "gmsh.dll" or name.startswith("libgmsh"):
                exact.append(os.path.join(d, name))
    exact.sort(key=len, reverse=True)
    return exact


def _preload_libgmsh():
    import ctypes

    for path in _find_libgmsh(_candidate_gmsh_lib_dirs()):
        try:
            ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
            return path
        except Exception:
            continue
    return None


def _import_gmsh():
    try:
        import gmsh
    except ImportError as exc:
        raise RuntimeError(
            "The 'gmsh' Python module is required for contour meshing but is not "
            "installed in FreeCAD's Python environment. Install it with "
            "'pip install gmsh' into the same environment FreeCAD uses."
        ) from exc

    try:
        gmsh.initialize()
        return gmsh
    except Exception:
        pass

    preloaded = _preload_libgmsh()
    try:
        gmsh.initialize()
        return gmsh
    except Exception as exc:
        detail = str(exc)
        hint = (
            "The 'gmsh' Python wrapper is installed, but its native library "
            "(libgmsh.so) could not be loaded, so meshing can't run. This is a "
            "packaging problem,  it is common in sandboxed "
            "FreeCAD installs (e.g. Flatpak/Snap) where the pip 'gmsh' wheel's "
            "shared library isn't found.\n"
            "Fixes:\n"
            "  - Install a gmsh build that bundles its library, e.g. reinstall "
            "with 'pip install --force-reinstall gmsh' into FreeCAD's Python.\n"
            "  - Or download the Gmsh SDK from gmsh.info and point the "
            "GMSH_LIB_DIR environment variable at its 'lib' folder before "
            "starting FreeCAD.\n"
            f"Underlying error: {detail}"
        )
        if preloaded:
            hint += f"\n(Tried preloading {preloaded}.)"
        raise RuntimeError(hint) from exc


def generate_uniform_mesh(shape, element_size: float) -> UniformMesh:
    """Mesh `shape` with gmsh at a uniform element size (mm).

    Raises RuntimeError if gmsh is unavailable, the size is unsafe, or no
    triangles were produced.
    """
    if element_size is None or element_size < MIN_ELEMENT_SIZE:
        raise RuntimeError(
            f"Element size {element_size} mm is too small; minimum is {MIN_ELEMENT_SIZE} mm."
        )
    estimate = estimate_triangle_count(shape, element_size)
    if estimate is None or estimate > TRIANGLE_HARD_CAP:
        raise RuntimeError(
            f"That element size would create about {estimate:,} triangles, over "
            f"the {TRIANGLE_HARD_CAP:,} safety limit. Choose a coarser resolution."
        )

    gmsh = _import_gmsh()

    tmp = tempfile.NamedTemporaryFile(suffix=".brep", delete=False)
    tmp.close()
    brep_path = tmp.name

    try:
        shape.exportBrep(brep_path)
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.option.setNumber("General.NumThreads", 0)
            gmsh.model.add("dfm_contour")
            gmsh.model.occ.importShapes(brep_path)
            gmsh.model.occ.synchronize()

            gmsh.option.setNumber("Mesh.MeshSizeMin", element_size)
            gmsh.option.setNumber("Mesh.MeshSizeMax", element_size)
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
            gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
            gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
            gmsh.option.setNumber("Mesh.Algorithm", 6)

            gmsh.model.mesh.generate(2)

            node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
            tag_to_index = {}
            vertices = []
            for i, tag in enumerate(node_tags):
                tag_to_index[int(tag)] = i
                vertices.append(
                    (
                        float(node_coords[3 * i]),
                        float(node_coords[3 * i + 1]),
                        float(node_coords[3 * i + 2]),
                    )
                )

            triangles = []
            cad_normals = []
            for _dim, surf_tag in gmsh.model.getEntities(2):
                s_tags, _s_coords, s_params = gmsh.model.mesh.getNodes(
                    2, surf_tag, includeBoundary=True, returnParametricCoord=True
                )
                if len(s_tags) == 0:
                    continue
                try:
                    normals_flat = gmsh.model.getNormal(surf_tag, s_params)
                except Exception:
                    normals_flat = None

                node_normal = {}
                if normals_flat is not None and len(normals_flat) == 3 * len(s_tags):
                    for k, nt in enumerate(s_tags):
                        node_normal[int(nt)] = (
                            float(normals_flat[3 * k]),
                            float(normals_flat[3 * k + 1]),
                            float(normals_flat[3 * k + 2]),
                        )

                e_types, _e_tags, e_node_tags = gmsh.model.mesh.getElements(2, surf_tag)
                for etype, enodes in zip(e_types, e_node_tags):
                    if int(etype) != 2:
                        continue
                    for j in range(0, len(enodes), 3):
                        t0, t1, t2 = int(enodes[j]), int(enodes[j + 1]), int(enodes[j + 2])
                        triangles.append((tag_to_index[t0], tag_to_index[t1], tag_to_index[t2]))
                        n0 = node_normal.get(t0)
                        n1 = node_normal.get(t1)
                        n2 = node_normal.get(t2)
                        if n0 and n1 and n2:
                            cad_normals.append(
                                _normalize3(
                                    n0[0] + n1[0] + n2[0],
                                    n0[1] + n1[1] + n2[1],
                                    n0[2] + n1[2] + n2[2],
                                )
                            )
                        else:
                            cad_normals.append(None)
        finally:
            gmsh.finalize()
    finally:
        try:
            os.unlink(brep_path)
        except OSError:
            pass

    if not triangles:
        raise RuntimeError(
            "gmsh produced no triangles. Check that the object is a valid solid or shell."
        )

    App.Console.PrintMessage(
        f"DFM contour: meshed at {element_size:.3f} mm -> "
        f"{len(vertices)} nodes, {len(triangles)} triangles.\n"
    )
    return UniformMesh(vertices=vertices, triangles=triangles, cad_normals=cad_normals)
