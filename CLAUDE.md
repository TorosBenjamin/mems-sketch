# Working on mems-sketch

Several sessions work on this repository at the same time. Follow
`CONTRIBUTING.md`; in short:

- **Never commit to or push `development` or `main`.** GitHub rejects direct
  pushes to both, for admins too.
- **Every piece of work gets its own branch** from the latest
  `origin/development` (`feature/…`, `fix/…`, `refactor/…`, `docs/…`,
  `perf/…`). If the main checkout has uncommitted changes that are not yours,
  leave them alone and work in a git worktree.
- **Merge through a pull request into `development`** (`gh pr create --base
  development`). It can be merged only when CI is green and the branch is up
  to date with `development`: if it is behind, `git fetch origin && git merge
  origin/development`, resolve conflicts keeping both sides' work, push, and
  wait for CI again. Don't use `--admin` or force-push to get around this.
- **`main` changes only by a pull request from `development`.**
- Before pushing: `uv run --extra dev ruff check . && uv run --extra dev ruff
  format --check . && uv run --extra dev python -m pytest -q` (GUI tests run
  offscreen; no windows open).
