<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->
<!-- SPDX-FileNotice: Part of the DFM addon. -->

# DFM

A FreeCAD workbench that analyses 3D models for Design for Manufacturing (DFM)
issues, helping designers catch problems before they reach production.

This workbench is in early development and your feedback shapes it. If
something looks wrong, behaves unexpectedly, or a feature would help your
workflow, please open an issue!

![DFM Workbench](https://raw.githubusercontent.com/ryankembrey/FreeCAD-DFM-Workbench/refs/heads/main/.github/images/sharp_internal.png)

---

## DFM Rules

Plastic Injection Molding is pre-configured out of the box. Rules, processes,
and materials are fully configurable for any manufacturing workflow.

- **Draft angle analysis:** detects faces with insufficient taper for mould ejection
- **Wall thickness analysis:** finds sections too thin or thick for the process and material
- **Undercut detection:** identifies geometry that prevents mould separation
- **Sharp corner detection:** finds sharp internal and external corners

## Workflow

- **Interactive results:** failing faces highlighted directly in the 3D viewport
- **Results history:** track regressions and resolutions throughout the design process
- **Customisation:** add manufacturing processes and edit materials
- **Export:** analysis results exportable to CSV

## Requirements

- FreeCAD 1.1+ - Officially supported
- FreeCAD 1.0  - Should work, not tested
- FreeCAD 0.x  - Not supported
