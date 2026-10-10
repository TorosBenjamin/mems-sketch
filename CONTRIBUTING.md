# Contributing

## Branches

| Branch | What it is | How it changes |
|---|---|---|
| `main` | Releases | Only by a pull request **from `development`** |
| `development` | The integration branch everyone works from | Only by pull requests from work branches |
| work branches | One piece of work each | Pushed freely by whoever owns them |

Nobody pushes to `main` or `development` directly, admins included: GitHub
rejects it. Several people (and several Claude sessions) work on the project
at the same time, and this keeps each piece of work separate until it is
tested against the latest `development`.

## Doing a piece of work

1. **Start from the latest `development`:**

   ```bash
   git fetch origin
   git switch -c feature/short-topic origin/development
   ```

   Name the branch by the kind of change: `feature/…`, `fix/…`,
   `refactor/…`, `docs/…`, `perf/…`. If your checkout has unrelated
   uncommitted work, use a worktree instead of switching:
   `git worktree add ../mems-sketch-topic -b feature/topic origin/development`.

2. **Commit in small steps.** Each commit leaves the tests passing. Say why
   in the message, not only what.

3. **Check locally before pushing:**

   ```bash
   pip install -e ".[dev]"      # once
   ruff check . && ruff format --check .
   pytest -q                    # GUI tests run headless (offscreen); no windows open
   ```

   If you changed the C++ geometry library (`src/geom/`) or the engine
   (`src/engine/`), also build and run their tests (setup in
   [`src/geom/README.md`](src/geom/README.md) and
   [`src/engine/README.md`](src/engine/README.md)):

   ```bash
   cmake --build build/geom && ctest --test-dir build/geom --output-on-failure
   cmake --build build/engine && ctest --test-dir build/engine --output-on-failure
   ```

   With Open CASCADE in `build/deps/`, `pip install -e .` also builds both
   into the package, as `mems_sketch._geom` and `mems_sketch._core`. The
   package needs them: they build every geometry.

4. **Push and open a pull request into `development`:**

   ```bash
   git push -u origin feature/short-topic
   gh pr create --base development --fill
   ```

   Describe what changed and how it was checked (tests, screenshots for UI
   changes). Name the issues it fixes with `Closes #12` (or *Fixes*,
   *Resolves*) in the title or description: merging into `development`
   closes them (`.github/workflows/close-issues.yml`). GitHub itself only
   does this on merges into `main`. Write "part of #12" for an issue the
   pull request doesn't finish.

5. **Merge when CI is green and the branch is up to date.** GitHub requires
   both: the **CI** check must pass, and the branch must contain the latest
   `development`. CI runs only what the change can affect
   (`.github/scripts/changed_areas.py`): a change to the GUI runs the GUI's
   tests, one to the Python package all Python tests, one to the engine or
   the geometry library also their C++ builds and tests, and one to the
   documentation alone runs nothing. Its last job, **CI**, passes when
   everything that ran passed. If `development` moved on,
   bring it in and let CI run again:

   ```bash
   git fetch origin
   git merge origin/development     # resolve conflicts keeping both sides' intent
   git push
   ```

   Then merge the pull request (`gh pr merge --merge --delete-branch`). The
   branch is deleted after merging.

Never force-push a branch someone else may have fetched, and never bypass
the rules (for example `gh pr merge --admin`).

## Releasing to `main`

A pull request into `main` is a release: merging it publishes one.

1. Set the new version in `pyproject.toml`, through a pull request into
   `development` as any change.
2. Open a pull request from `development` into `main`
   (`gh pr create --base main --head development`). Its checks fail when it
   comes from another branch or when the version is already released; CI
   must be green, as for `development`. The wheels and the app are not built
   again here: every change was built on its way into `development`.
3. Merging it starts the release (`release.yml`): the wheels (`wheels.yml`)
   and the app (`app.yml`) are built on every platform (pull requests into
   `development` build them for Linux alone), each started with
   `--self-test` first: a Windows installer and portable zip, a macOS disk
   image and a Linux AppImage. Then a GitHub release `v<version>` is
   published on the merge commit with all of them and notes generated from
   the merged pull requests; edit the notes on the releases page if needed.

`mems-sketch --self-test` checks an installed or bundled app (engine, rule
checks in another process, GDS, the window); `packaging/` holds what builds
the app.

## Conventions

- Code follows the style around it; `ruff` settles formatting.
- The backend (everything outside `mems_sketch/gui`) never imports Qt; a test
  enforces it.
- A shape kind, an edit command, a tool window, an export or file format is
  added as described in [Extending](docs/developer/extending.md).
- A visible change to the GUI comes with fresh pictures:
  `python docs/screenshots.py` regenerates `docs/images/`; update the
  [user guide](docs/user/README.md) where the change shows.
- Design notes and plans for larger changes go in `docs/superpowers/specs/`
  and `docs/superpowers/plans/`.
