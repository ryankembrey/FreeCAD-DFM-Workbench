#  ***************************************************************************
#  *   Copyright (c) 2025 Ryan Kembrey <ryan.FreeCAD@gmail.com>              *
#  *                                                                         *
#  *   This file is part of the FreeCAD CAx development system.              *
#  *                                                                         *
#  *   This library is free software; you can redistribute it and/or         *
#  *   modify it under the terms of the GNU Library General Public           *
#  *   License as published by the Free Software Foundation; either          *
#  *   version 2 of the License, or (at your option) any later version.      *
#  *                                                                         *
#  *   This library  is distributed in the hope that it will be useful,      *
#  *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
#  *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
#  *   GNU Library General Public License for more details.                  *
#  *                                                                         *
#  *   You should have received a copy of the GNU Library General Public     *
#  *   License along with this library; see the file COPYING.LIB. If not,    *
#  *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
#  *   Suite 330, Boston, MA  02111-1307, USA                                *
#  *                                                                         *
#  ***************************************************************************


import FreeCAD
import FreeCADGui as Gui
from PySide6 import QtCore, QtGui, QtWidgets
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face
import Part

from . import DFM_rc


class TaskResults:
    def __init__(self, results, target_object, process: str):
        self.target = target_object
        self.target_shape = target_object.Shape
        self.target_label = target_object.Label
        self.results = results
        self.process = process

        self.form = Gui.PySideUic.loadUi(":/ui/task_results.ui")
        self.form.setWindowTitle("DFM Analysis")

        self.form.leTarget.setReadOnly(True)
        self.form.leProcess.setReadOnly(True)

        self.form.leTarget.setText(self.target_label)
        self.form.leProcess.setText(self.process)

        Gui.Control.showDialog(self)
        Gui.Selection.clearSelection()
