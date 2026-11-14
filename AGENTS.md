# Working on the DFM Workbench (for AI agents)

A FreeCAD 1.1+ Python workbench that checks parts against design for manufacturing rules. Read
`docs/_notes/codebase-notes.md` for how the code fits together before changing it.

## The owner's rules

- **Never open pull requests** on this repository. Hand over the changed files; the owner applies
  them locally.
- **The documentation is part of every change.** A feature isn't done until the site describes it.
  The docs site (`docs/`, published to GitHub Pages) is written and maintained by AI agents.

## Documentation: what to do when you change something

Most of the site is generated from the code by `docs/build.py`. Run it after any change:

```bash
pixi run docs        # regenerate docs/ and build the site (docs/build/site)
pixi run serve       # the same, then preview at http://localhost:8000
pixi run docs-check  # fails if anything generated is out of date
```

Without pixi, `QT_QPA_PLATFORM=offscreen python docs/build.py --no-shots` regenerates the pages
(it needs PySide6, PyYAML and OCP 7.8, as the tests do). Commit everything it changes, images
included.

**Screenshots** are taken by code, in a real FreeCAD from the pixi environment on a virtual display
(needs `xvfb-run` from the system). `pixi run docs` retakes them only when the workbench's code,
resources, example parts or the shot code changed (about 6 minutes); otherwise it skips them.
`python docs/build.py --shots` retakes all of them; `--no-shots` leaves them alone.

| You changed | Also do |
| :-- | :-- |
| A rule's label, description, unit or shape (`core/rules.py`), its diagram (`gui/rule_information/_diagrams.py`), or a check's message placeholders | Run `pixi run docs`. Nothing to write by hand. |
| A value, material, message or criticality in a built-in process (`core/processes/*.yaml`) | Run `pixi run docs`. Update `docs/_data/citations.yaml` for every value you added or changed (see below). If you removed a value, remove its claim: the build fails on claims that point at nothing. |
| A new rule | Everything above. Optionally write `docs/_authored/rules/<rule-slug>.md` ("How it's checked", "How to fix"), which is placed under the rule's diagram. |
| A new built-in process | Run `pixi run docs`; it gets its own pages. Add its file name to `PROCESS_ORDER` in `docs/_pipeline/model.py` to place it in the list. Add claims for its values. |
| A tool, panel, command, preference or anything a user sees | Update or add a hand-written page in `docs/content/` and add new pages to the `nav` in `docs/zensical.toml` (outside the `BEGIN GENERATED` blocks). Add a screenshot of it (below). |
| A panel's layout or what it shows | Nothing by hand: its screenshots are retaken. Look at them (`docs/content/assets/shots/`) and fix any page whose text no longer matches the picture. |
| Something that changes what an existing guide or concept page says | Fix that page in the same change. Search `docs/content` for the feature's name. |

### Screenshots

Every picture of the workbench on the site is made by a shot in `docs/_pipeline/shots/`:

- `panels.py`: windows and panels that need no model (Process Library, dialogs, preference pages).
- `scenes.py`: anything with a part in the 3D view. Shots drive the real panels the way a user
  does (the methods their buttons call) on the example parts in `resources/examples`.

A shot is a function decorated with `@shot("<id>", "panel" | "scene", "<what it shows>")` that
returns a `QImage`. Its image is `docs/content/assets/shots/<id>.png`; use it in a page as
`[![<what it shows>](../assets/shots/<id>.png)](../assets/shots/<id>.png)` (adjust the `../`): the
link lets readers open it full size. The build fails if a page uses a
shot that doesn't exist, or a shot isn't used by any page.

- Never hand-make or edit a screenshot, and never put one in `docs/content` yourself.
- Take a shot after the thing it shows has finished drawing (`ctx.pump()`), and crop to what the
  page is about (`ctx.view_and_task()`, `ctx.crop(widget)`).
- Look at every new or changed shot before you hand the change over.
- Shots must not show anything machine-specific (paths, user names): blank it in the shot.

### Never edit generated files

`docs/build.py` overwrites these, and deletes anything there it didn't write:

- `docs/content/reference/` (rule and process pages)
- `docs/content/assets/diagrams/`, `docs/content/assets/shots/`, `docs/content/assets/logo.svg`
- `docs/content/reference/preferences.md`, `docs/content/reference/features.md`
- `docs/content/changelog.md` (rebuilt from git tags each build, not committed)
- `docs/_data/shots.json` (the screenshots' fingerprint)
- `docs/content/citations/coverage.md`, `docs/content/citations/bibliography.md`
- the blocks between `# BEGIN GENERATED` and `# END GENERATED` in `docs/zensical.toml`

Each generated Markdown file starts with a comment naming its sources. Change those instead.

### Citations (`docs/_data/citations.yaml`)

Every value in the built-in processes can carry a claim saying where it comes from: `published`
(a source gives the figure), `derived` (worked out from a source's figures) or `judgement`
(no source gives a figure). A value with no claim shows as "Not yet sourced". The file's header
explains the format.

- **Never invent a source, a URL or a figure.** If you don't know where a value comes from, leave
  it without a claim and say so to the owner.
- Put the source's original figure in the note when it differs in form (inches, degrees from
  level...).
- Values marked "Ours" in the process files' comments are `judgement`.

### Writing the hand-written pages

- Write for designers using the workbench, not for developers. Plain words, short sentences,
  sentence case headings, second person ("you").
- Use the workbench's own names for things, exactly as the UI shows them (**DFM Analysis**,
  **Process Library**, *Aim for*, *At least*).
- Don't type numbers that come from the code (values, counts of rules or materials) into
  hand-written pages: they go stale. Link to the generated page that shows them.
- Keep `docs/content/index.md` (the landing page) a short overview.
- Notes for agents go in `docs/_notes/` (not published), never in `docs/content/`.

## Tests

```bash
QT_QPA_PLATFORM=offscreen python tests/run_tests.py
```

`tests/test_docs.py` runs `docs/build.py --check`: it fails when generated docs are stale or
citations point at values that don't exist.
