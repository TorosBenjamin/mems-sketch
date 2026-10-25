# Processes and process libraries

A process is what a fab makes: its **layers** (with their GDS numbers), the
**layer stack** they form, **constants** (thicknesses, the release undercut)
and the **design rules** a layout must keep. Components are drawn against it,
and the rule checks use it. See [Components → The process](components.md#the-process)
for the Process tab itself.

## The example processes

The bundled `mems` library has three **examples**. They have typical, round
values, modelled on common kinds of process, and are not any foundry's rules:
use them to learn, to sketch, and as a start for your own.

| Process | What it is | Levels (bottom to top) |
|---|---|---|
| `mems.surface` | One released structural layer over a sacrificial oxide, with wiring under it | wiring, device (anchor), metal |
| `mems.polysilicon` | Two-poly surface micromachining: a poly0 ground layer, poly1 over oxide1, poly2 over oxide2, metal on poly2 | poly0, poly1 (anchor: anchor1, dimple), poly2 (anchor: anchor2, via: via12), metal |
| `mems.soi` | Silicon on insulator: a 25 µm device layer etched through, released by etching the buried oxide, with metal pads and an optional back-side cavity | cavity, device (anchor), metal |

Choose one under **Process** at the top of the Process tab. Each has width and
spacing rules and the **anchored** and **release** checks (as warnings, with
the undercut a constant, `process.undercut`). `mems.polysilicon` and `mems.soi`
also have enclosures (anchors and vias inside the layers they join, metal on
its layer); `mems.surface`, the simplest, has only width and spacing rules
(device and wiring) and the device layer's anchored and release rules, so a
process copied from it starts without enclosures. Its `wiring` layer, under
the sacrificial oxide, reaches anchors that the structure boxes in: a folded
flexure's anchor has a port on it (`anchor`). For a pad that stays put at
release, use `mems.bond_pad` (`std.bond_pad` is not anchored). Thicknesses
are constants too (`process.t_poly1`), for expressions and for what reads them
later.

The `mems` components draw on `level` and `level.anchor`, so they work on any
of them: a comb drive placed on poly2 gets poly2's anchors. Their defaults are
sized for `mems.surface` (2 µm lines and gaps, a 2 µm undercut): on a process
with a larger undercut or minimum width, widen the anchors and fingers to its
rules.

## Writing your own

A fab's process belongs in a **process library**: a folder holding the
process, that every project of that fab uses as a library. Start one from an
example:

```console
mems-sketch-cli new-process my_fab --from mems.polysilicon
```

That writes `my_fab/project.yaml` (a library, no top component) and
`my_fab/processes/my_fab/process.yaml`, a copy of the example to edit
(`--from empty` starts with nothing). Add the folder to a project as a library
(Components **+**, or `libraries: {fab: ../my_fab}` in `project.yaml`) and
choose `fab.my_fab` as its process.

### From the handbook to `process.yaml`

A fab's design handbook lists layers and a table of rules. Each row becomes a
rule of one of the [rule kinds](components.md#design-rules):

| Handbook says | Rule |
|---|---|
| Minimum POLY1 line width 2 µm | `poly1_min_width: {kind: min_width, layers: [poly1], value: 2}` |
| Minimum POLY1 spacing 2 µm | `poly1_min_space: {kind: min_space, layers: [poly1], value: 2}` |
| POLY1 must enclose ANCHOR1 by 2 µm | `anchor1_in_poly1: {kind: enclosure, layers: [poly1, anchor1], value: 2}` |
| METAL must lie on POLY2 | `metal_on_poly2: {kind: inside, layers: [metal, poly2]}` |
| DIMPLE 3 µm from ANCHOR1 | `dimple_clear: {kind: separation, layers: [dimple, anchor1], value: 3}` |
| Minimum ANCHOR1 area 9 µm² | `anchor_area: {kind: min_area, layers: [anchor1], value: 9}` |
| Release holes at most 30 µm apart | `poly1_release: {kind: release, layers: [poly1, anchor1], undercut: 15}` |

A whole process:

```yaml
description: Our fab's two-poly process, rev. C
constants: {undercut: 3, t_poly1: 2}
layers:
  poly1: {gds: [4, 0]}
  anchor1: {gds: [2, 0]}
levels:
- layer: poly1
  roles: {anchor: anchor1}
default_level: poly1
grid: 0.005          # the fab's grid (µm); curves are checked within `chord`
rules:
  poly1_min_width: {kind: min_width, layers: [poly1], value: 2}
  anchor1_in_poly1: {kind: enclosure, layers: [poly1, anchor1], value: 2, message: see handbook 4.2}
  poly1_release: {kind: release, layers: [poly1, anchor1], undercut: process.undercut, severity: warning}
```

- **Layers** are named as you like; their GDS layer and datatype are what
  exports write.
- **Levels** are the stack, bottom to top. A level is a layer, with **roles**
  for the layers that belong to it (`anchor`, `dimple`, `via`: any name), so a
  component draws `level.anchor` and works on every level that has one.
- **Constants** can be used in rules (`process.undercut`) and in every
  component's expressions (`process.min_gap`).
- A rule's `message` is shown with its violations; `severity: warning` reports
  it without failing `check`.

Projects using the library follow its process when it changes, except where
they [changed it](components.md#processes-from-a-library), each change kept in
their `project.yaml` with its reason.

### Foundry rules and their licences

Foundries publish their handbooks for their customers, under their own terms:
often for your own, non-commercial use, and not to be passed on. A process
library you write from one is for you and your team; check the handbook's
terms before you share it. mems-sketch ships only its own examples.

### What the rules cannot say yet

Some handbook rules have no rule kind yet; check them by hand (or waive what
the checks report instead):

- **Anchored through another layer:** poly2 held by poly1 through a via counts
  as floating for `anchored` on poly2 and anchor2 (one anchor layer per rule).
- **Aspect ratio** (a trench's width against the layer's thickness), and
  rules that depend on a feature's length (long thin beams).
- **A pattern density map** (DRC-5, #86): the `density` rule checks windows,
  but there is no map of them on the canvas yet.
- **Exact overlaps** (a via must be covered by both layers *and* lie where
  they overlap): write it as two enclosures, as `mems.polysilicon` does.
