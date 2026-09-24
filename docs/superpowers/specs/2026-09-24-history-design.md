# History: what changed, from git

## Goal

A project is a folder of YAML files that is meant to live in git. A YAML diff is
hard to read, and one number can move half the device. The History tool window
shows what changed, in words a designer uses and on the canvas.

The feature only reads the repository. People commit, branch and merge with their
usual git tools. Committing from the app can come later, once the viewer has
proven itself.

## Reading versions (`storage/git.py`)

- The code runs the `git` program with `subprocess`. It uses no library.
- A folder outside a repository, or a machine without git, has no history.
  Nothing fails.
- `commits(folder)` runs `git log -- .` in the project folder, so only commits
  that touched the project are listed.
- `load_at(folder, rev)` loads the project as it was in a commit:
  - `git archive <rev> -- <folder>` goes into a temporary folder, and the
    normal loader reads it from there.
  - Imported GDS files come from the same commit.
  - Libraries are the current ones, found relative to the real folder
    (`load_project(..., libraries_from=)`). A library outside the repository
    has no history of its own here.
- It returns None when the project did not exist in that commit. The comparison
  then shows everything as added.

## What changed (`core/diff.py`)

`diff_projects(old, new)` returns `Change`s with these fields:

| Field | Values |
|---|---|
| action | added / removed / changed |
| group | a component's name, or Project / Process / Imports |
| what | component, description, parameter, point, shape, layer, constant, import, library |
| subject | the name, for shapes their path in the tree (`cut › slot`) |
| details | what changed: `default 45 → 60`, `switched off` |
| component, path | where to go when the change is clicked: the shape in the newer version, or for a removed shape what held it |

How versions are matched:

- Parameters, points, layers, constants, imports and components are matched by
  name.
- Shapes are matched per child list:
  - Named shapes are matched by name.
  - The others go through `difflib.SequenceMatcher` over their serialised
    content. Inserting an unnamed shape then reads as one addition, not a change
    to every shape after it.
  - Leftover shapes of the same kind are paired in order.
  - Matched shapes are compared field by field, leaving out their child lists.
    The comparison then goes into those lists.
- A renamed shape reads as a change of `name` when nothing else fits it better.
  A rename together with other edits can read as removed plus added.

`geometry_changes(old, new)` returns the material added (`new − old`) and removed
(`old − new`), per layer, as KLayout regions.

## Session API (`editing/history.py`, `session.history`)

- `available`, `commits()`.
- `version(rev)`: `None` is the project being edited, in memory. Other versions
  are cached by sha.
- `uncommitted()` compares HEAD with the project now, unsaved edits included.
- `commit(rev)` compares a commit's first parent with the commit.
- `compare(old, new)` compares any two versions.
- `geometry(comparison, component)` works on the given component with default
  parameters:
  - Earlier versions build in their own `Compiler`, so the editor's cache is not
    flooded.
  - When a version does not build, it raises and says which version failed.
- Opening another project forgets the cached versions.

## GUI (`gui/history_panel.py`)

- The History tool window sits on the right, with a clock icon.
- The panel has two parts:
  - The version list on top: *Uncommitted changes* first, then the commits, each
    with its subject and a short date. The tooltip has the sha, author and full
    date.
  - The change list below, grouped by component. Each change has an icon: plus
    for added, minus for removed, an orange dot for changed.
- The panel title shows which versions are compared.
- Right-click a commit and choose *Compare with the design now*.
- Clicking a change:
  - opens its component;
  - selects the shape, or what held a removed one;
  - pans to it, keeping the zoom.
- While the panel is open, the canvas draws the difference for the component in
  the current tab:
  - added material tinted green;
  - removed material hatched red. The hatching makes it readable without
    colour, and it shows that the material is gone.
  - These layers sit under the selection highlight.
- When the panel is closed, nothing extra is drawn or computed.
- Refreshing:
  - comparisons against the design now follow every edit;
  - the commit list is read again on save, when the panel is shown, and with the
    Refresh button (after committing outside the app).
- A project outside git shows a note on how to get history.

## Not now

- Committing, staging, branches and restoring a version.
- Comparing with parameter values other than the defaults.
