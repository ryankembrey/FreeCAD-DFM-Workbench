# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Single definition of where the DFM addon stores its FreeCAD preferences."""

import FreeCAD as App  # type: ignore

DFM_PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/DFM"


def dfm_params():
    """The DFM preference group (a FreeCAD ``ParameterGrp``)."""
    return App.ParamGet(DFM_PARAM_PATH)
