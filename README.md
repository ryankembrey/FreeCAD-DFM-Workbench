<div align="center">

# DFM Workbench

![License](https://img.shields.io/badge/license-LGPL--2.1-blue)

A FreeCAD workbench that analyses 3D models for Design for Manufacturing (DFM)
issues, helping designers catch problems before they reach the factory.

<img src=".github/images/overview.png" alt="DFM Workbench results panel with findings highlighted on a model" width="100%">

</div>

The philosophy of this project is to give more power to hobbyists and small businesses to have access to powerful DFM tools that not only critique, but teach as well.

Another key point is customisability. Every rule is 100% customisable. The Process Library is the main way you can do this, but for experts, you can tweak the parameters for the geometric analyzers in the DFM Preferences page.

<table>
<tr>
<th align="left">Currently supported rules</th>
<th align="left">Support manufacturing processes (currently)</th>
<th align="left">Support contours</th>
<th align="left">Other features</th>
</tr>
<tr valign="top">
<td>

- Maximum and minimum wall thickness
- Minimum draft angle
- Undercut detection
- Sharp internal and external corners
- Maximum support material
- Maximum bridge span
- Maximum bed contact area
- Maximum overhang angle
- Maximum cusp height

</td>
<td>

- FDM 3D printing
- Plastic injection molding

</td>
<td>

- Thickness
- Draft angle

</td>
<td>

- Generate professional pdf reports
- Export to CSV

</td>
</tr>
</table>

<p align="center">
  <img src=".github/images/report_title.png" alt="PDF report cover page" width="49%">
  <img src=".github/images/report_thickness.png" alt="PDF report page for a wall thickness rule" width="49%">
</p>

## Contours

Analyse wall thickness or draft angles of your parts, and create nice graphics.

<p align="center">
  <img src=".github/images/thickness_analysis.png" alt="Wall thickness contour on a part" width="100%">
</p>

## Process Library

<p align="center">
  <img src=".github/images/process_library1.png" alt="Process Library in list layout" width="49%">
  <img src=".github/images/process_library.png" alt="Process Library in grid layout with rule diagrams" width="49%">
</p>

<p align="center">
  <img src=".github/images/edit_feedback.png" alt="Editing a rule's values and feedback messages" width="70%">
</p>

## Installation

Install directly from the **FreeCAD Addon Manager**.

<div align="center">

| Version | Support |
|---------|---------|
| FreeCAD 1.1+ | Officially supported |
| FreeCAD 1.0 | Should work, not tested |
| FreeCAD 0.x | Not supported (requires PySide6) |

</div>

## Contributing

If you'd like to help out, here are some ways you might consider contributing:

- Creating bug reports
- Suggesting new features
- Submitting code to fix bugs or add new features
- Creating user documentation on FreeCAD Wiki
- Sharing DFM knowledge for any manufacturing process (through submitting an issue would be great!)
- Creating example models for manufacturing processes that can be bundled with the workbench
- Creating tutorials either through a website or a video
