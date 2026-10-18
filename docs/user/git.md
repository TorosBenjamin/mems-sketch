# Git: committing and history

For a project saved in a git repository, the **Commit** panel (on the left
edge) lists what changed since the last commit and commits it, and the
**Git** panel (along the bottom) lists the commits, what each changed, and
brings back earlier versions.

![The Commit panel: the top comb went from 16 fingers to 20, with the material added and removed on the canvas](../images/git.png)

## The Commit panel

The changes since the last commit, saved or not, are listed by what holds
them: one row per component (with its private ones), and rows for the
project, its process and its imports.

- **A row unfolds into each change**: `pitch  default 20 → 24 µm`,
  `comb_top  fingers 16 → 20`, `pad_left  added`. A change to several fields
  of one thing (a parameter's default, minimum and maximum) unfolds again,
  one line per field. Click a change to open its component and select the
  shape.
- **Names are coloured** by what happened: green added, blue changed, grey
  removed. Other files in the project's folder that git sees changed are
  listed with them; files git does not track yet are in **Unversioned files**,
  orange, with *Add to git* and *Ignore* on the right-click menu.
- **On the canvas**, material added is tinted green and material removed is
  hatched red, in the component you are editing (with default parameters). A
  one-number change that moves half the device shows at a glance.
- The panel follows your edits, saving, and the app coming back to the front
  (in case you used git meanwhile). Its header shows the branch, and how many
  commits it is ahead (↑) or behind (↓) the one it tracks.

### Committing

**Check what goes into the commit.** A component is the unit: git commits
files, and each component is a file of its own. Files git does not track yet
are left out unless you check them.

Type a message and press **Commit**. A message is suggested from the checked
changes (`Change parameter pitch; add pad_left`); leave the box empty to use
it. **Amend** replaces the last commit with this one, starting from its
message.

- The project is saved first, and **only the checked files are committed**.
  The project file lists the components, so it goes with a component added,
  removed or moved to another category. Files you staged elsewhere in the
  repository stay staged and are named under the box.
- Git needs to know who you are, once: `git config --global user.name "Your
  Name"` and `git config --global user.email you@example.com`. The panel says
  so if it is missing.

### Rolling back

Right-click a row and **Roll back** to bring that component (or the project,
process or imports) back as it is in the last commit. It is an ordinary edit:
Undo takes it back. A component added since the last commit is removed. A
changed file outside the design is put back after you confirm, and that
cannot be undone.

## The Git panel

The commits that changed the project, newest first. Choose one to see what it
changed, beside the list and on the canvas.

- Right-click a commit and **Compare with the design now** to see everything
  since then.
- **Restore the project as it was**, or **Restore &lt;component&gt; as it
  was** for the one you are editing: an ordinary edit, which Undo takes back,
  and nothing in git changes. The Commit panel then shows what the restore
  changed; commit it to keep it.

## Starting a repository

A saved project that is not in a repository gets **Initialize git here**.
Branches, merging, push and pull stay with your usual git tools for now.
