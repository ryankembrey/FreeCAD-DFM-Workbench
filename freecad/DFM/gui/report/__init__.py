# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""PDF reports of DFM results, written with Qt's QPdfWriter (no extra dependencies).

Modules
-------
``settings``          ``ReportSettings``: every option, stored in the DFM preferences
``template``          ``ReportTemplate``: page and style; built-in and user YAML templates
``data``              ``ReportData``: a snapshot of the results, and which findings get images
``layout``            Lays the report out into pages, painted to the PDF and the preview
``capture``           ``ViewportCapture``: viewport images with findings highlighted
``exporter``          Captures images with a progress dialog and writes the file
``dialog``            ``ReportDialog``: the Export PDF report dialog
``preferences_page``  ``DFMPreferencesReports``: Preferences > DFM > Reports
"""
