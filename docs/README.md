# docs/

The source of the DFM Workbench documentation site, published to GitHub Pages by
`.github/workflows/docs.yaml` on every push to `main`. The site is built with
[Zensical](https://zensical.org) from plain Markdown.

```bash
pixi run docs        # regenerate everything from the code, then build the site
pixi run serve       # preview at http://localhost:8000
pixi run docs-check  # exit 1 if anything generated is out of date
```

The first `pixi install` writes `pixi.lock` at the repository root: commit it, as CI installs
exactly what it pins.

## What's here

| Path | What it is | Written by |
| :-- | :-- | :-- |
| `zensical.toml` | Site configuration and navigation | Hand, except the `BEGIN GENERATED` blocks |
| `content/` | The site's pages and files | Mixed, see below |
| `content/reference/` | A page per rule and per process | `build.py` |
| `content/assets/diagrams/` | Rule diagrams, light and dark | `build.py` |
| `content/citations/coverage.md`, `bibliography.md` | Where each value comes from | `build.py` |
| `content/assets/shots/` | Screenshots of the workbench | `build.py`, in FreeCAD |
| `content/reference/preferences.md`, `features.md` | Preference pages, recognized features | `build.py` |
| `content/changelog.md` | Changes per version, from git tags (not committed) | `build.py` |
| everything else in `content/` | Guides, concepts, the landing page | AI agents, by hand |
| `_data/citations.yaml` | Sources, and claims about each built-in value | Hand |
| `_authored/rules/<slug>.md` | Optional prose placed on a rule's page | Hand |
| `_pipeline/` | The generator (`build.py` runs it) | |
| `_notes/` | Notes for AI agents; never published | |
| `build/` | The built site (ignored by git) | `zensical` |

`AGENTS.md` at the repository root says what to update when the workbench changes.

## How the generator works

`build.py` imports the workbench behind the test suite's FreeCAD stand-ins
(`tests/recognition_support/freecad_stubs.py`), so it needs no FreeCAD and nothing in your own
FreeCAD settings or saved processes affects it. It reads:

- `Rulebook` in `freecad/DFM/core/rules.py` for each rule's name, description, unit and shape,
- `DIAGRAMS` in `freecad/DFM/gui/rule_information/_diagrams.py` for the diagrams, coloured by the
  workbench's own `themed_svg` for the site's light and dark schemes,
- each check's message placeholders,
- the built-in process files in `freecad/DFM/core/processes/`, directly (not the Process Registry,
  which would add your own saved processes),
- `_data/citations.yaml`.

It writes a file only when its content changed, so a rebuild with nothing new leaves git clean,
and it deletes generated files it no longer writes. The output is the same on every machine.

## Screenshots

Every screenshot is made by a shot in `_pipeline/shots/` (`panels.py`, `scenes.py`), run inside a
real FreeCAD from the pixi environment on a virtual display. You need Xvfb from your system
(Debian/Ubuntu: `sudo apt install xvfb xauth`; Fedora: `sudo dnf install xorg-x11-server-Xvfb xauth`). It works from a Wayland session too: FreeCAD always runs on the virtual X display, never on your desktop.

- FreeCAD runs with a throwaway user folder, so your own settings and saved processes never show.
- It uses a private font configuration with only the environment's fonts, so text renders the
  same on every machine.
- `_data/shots.json` holds a fingerprint of everything a shot could show (the workbench's code and
  resources, the example parts, the shot code, `pixi.lock`). When it matches, no FreeCAD runs.
- A retaken image replaces the old one only if it visibly differs, so rendering noise doesn't
  become commits.

`python docs/build.py --shots` retakes everything; `--no-shots` leaves the screenshots alone.
CI retakes them for the published site either way.
