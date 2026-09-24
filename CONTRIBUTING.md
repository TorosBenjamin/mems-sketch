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

4. **Push and open a pull request into `development`:**

   ```bash
   git push -u origin feature/short-topic
   gh pr create --base development --fill
   ```

   Describe what changed and how it was checked (tests, screenshots for UI
   changes).

5. **Merge when CI is green and the branch is up to date.** GitHub requires
   both: CI (lint and tests on Python 3.11 and 3.12) must pass, and the
   branch must contain the latest `development`. If `development` moved on,
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

Open a pull request from `development` into `main`
(`gh pr create --base main --head development`). A check fails any pull
request into `main` from another branch; CI must be green, as for
`development`.

## Conventions

- Code follows the style around it; `ruff` settles formatting.
- The backend (everything outside `mems_sketch/gui`) never imports Qt; a test
  enforces it.
- A shape kind, an edit command, a tool window or an export format is added
  as described under *Extending* in the README.
- Design notes and plans for larger changes go in `docs/superpowers/specs/`
  and `docs/superpowers/plans/`.
