# The Sketch language

**Status: design.** Nothing here is built yet beyond today's expressions,
which are already valid Sketch. The [steps](#steps) say in which order it
comes.

Sketch is the language of every value in a project: a field's expression
(`pitch * 2`), a component's derived values and checks, a shape's condition,
and later design rules and shapes made from other shapes. It works like the
formulas of a spreadsheet, not like a script: the YAML is the structure the
editor shows and changes, and each field holds a formula that describes a
value from other values. Nothing runs, nothing changes; values are worked out
when they are needed.

Today's expressions ([EXP-1 to EXP-5](requirements.md#expressions)) are
where it starts: every value can be an expression, safe arithmetic only, names
from parameters, constants, points and arrays, evaluated in dependency order
in double precision. Sketch keeps all of it.

Sketch is not a new grammar. It is a **profile of Python's expression
grammar**, as [Starlark](https://github.com/bazelbuild/starlark) is a profile
of Python: the syntax and the meaning of arithmetic are Python's, so engineers
can read it and today's expressions keep working. What Sketch adds is what no
existing language offers for layout: **types with units**, **geometry as
values** (layers and regions), **measurements and checks**, and errors that
point at the place in the text.

## Why not an existing language

Candidates looked at:

| | For | Against |
|---|---|---|
| [CEL](https://cel.dev) (Google) | Built for formulas in configuration; non-Turing-complete, type-checked, custom types, error positions, a specification | C++ implementation needs Abseil, Protocol Buffers, ANTLR and Bazel; different syntax (`a ? b : c`, `&&`, no `**`), so every project migrates; `int * double` is a type error; no units |
| Starlark | Python syntax, deterministic | A scripting language (statements, functions), dynamically typed, no mature C++ implementation |
| Jsonnet, CUE, Dhall, Nickel | Configuration languages | They generate configuration (they would replace the YAML, not live in its fields); mostly no C++ implementation |
| Lua, Luau | Small, embeddable | Imperative scripting: the opposite of what is wanted |
| muParser, ExprTk | Fast, tested C++ formula parsers | Numbers only: no types, units or geometry |
| KLayout DRC, Calibre SVRF | Rule languages | A Ruby library; proprietary. No open standard for rule decks |

Taking Python's expression grammar borrows the risky parts of a language (a
grammar people know, parsing, the meaning of arithmetic, already implemented
twice and compared bit for bit) and leaves only the parts specific to layout to
design. If much richer data (maps, records) is ever needed, CEL is where to
look again.

## Goals

- **Pure.** No side effects, no state. The same inputs give the same value,
  byte for byte on every platform ([QP-5](requirements.md#precision)).
- **Total.** Every expression finishes. There is no recursion and no unbounded
  repetition, so a library from anyone can be opened safely.
- **Strongly typed, checked before building.** Types and units are worked out
  from the names used; a mistake is shown in the field, at its place, as it is
  typed. No implicit conversions except a whole number to a number.
- **Never silently wrong** ([QR-3](requirements.md#robustness)): what cannot
  be computed is an error, with where.
- **Safe to share.** A project or library holds data, never code: Sketch has
  no files, network, time or randomness.
- **One implementation**, used by the engine and the editor alike.

Not goals: statements, functions defined by the user, changing a value,
loops other than over finite lists, strings beyond what messages need. What
truly needs those stays a Python plugin (a shape kind, a rule kind), as today.

## Where it is used

| Where | Type | Example |
|---|---|---|
| A field of a shape, modifier or placement | the field's type | `pitch * 2`, `level + 1` |
| A component's derived values | any | `gap = pitch - 2 * w_finger` |
| A parameter's limits and default | number | `max: size / 2` (exists) |
| A shape's condition | bool | `enabled: n_fingers > 0` |
| A component's checks ([CMP-11](requirements.md#components-and-parameters), #64) | bool | `gap >= process.min_gap` |
| A shape made from others | region | `sized(outline, 5 * um) - outline` |
| A design rule | violations | `width(device) < 2 * um` |

## The model

A component is a set of named values: its parameters (inputs), its derived
values, and every field of every shape. Each is an expression over the
others. Together they form a graph, worked out in dependency order; a cycle is
an error that names the values in it (as today). The graph is also what
decides what is rebuilt after a change
([QS-7](requirements.md#performance-and-scale)).

Nothing is assigned twice. A parameter is set by whoever places the
component, like an argument; inside, it is read-only. A derived value is
defined once.

## Names

| Name | What it is |
|---|---|
| `width` | a parameter of the component, or one of its derived values |
| `process.min_gap` | a process constant |
| `mass.center.x` | a point of a shape (exists); `mass.center` is the point itself |
| `i`, `j` | the indices of the copy inside an array modifier (exists) |
| `self` | in a modifier, the shape just before it (exists) |
| `level`, `level + 1`, `level.anchor` | layers relative to the component's level ([CMP-10](requirements.md#components-and-parameters)) |
| `layers.metal` | a layer by name; in a field whose type is a layer, `metal` is enough |
| `um`, `nm`, `mm`, `deg`, `rad` | units (below) |
| `pi` | π (exists) |

A name used in an expression that is none of these is an error at its place.
Names of the language (`abs`, `um`, `level`, …) cannot be parameter names
(today's `RESERVED_NAMES`, extended).

**Local names** inside one expression use Python's assignment expression:
`(r := width / 2) * r * pi`. The name exists only in that expression.

## Types

| Type | Values | Notes |
|---|---|---|
| `number` | decimals, with a unit dimension | length, area, angle or none |
| `int` | whole numbers | counts, indices; an `int` is a `number` where one is wanted |
| `bool` | `True`, `False` | conditions, checks |
| `point` | an (x, y) position | from shapes' points; `.x`, `.y` are lengths |
| `layer` | a layer of the stack | `level + 1`, `layers.metal` |
| `region` | geometry on a layer, exact | from layers and shapes; operations below |
| `list[T]` | finite lists of one type | `[2, 3, 4]`, `widths[i]` |
| `violations` | where a check fails, with boxes | the value of a rule |

Types are inferred: parameters have the type of their default (and their
field), process constants are numbers, functions declare what they take and
give. Nobody writes a type.

### Units

A number has a dimension: length (µm), area (µm²), angle, or none. The
arithmetic follows: `+`, `-` and comparisons need the same dimension; `*` and
`/` combine them (`width * width` is an area, `area / width` a length); `sqrt`
of an area is a length.

- A **plain number in a field** has the field's dimension: `x0: 5` is 5 µm, as
  today. So every existing project means what it meant.
- **Unit names** turn numbers into quantities where it is not clear:
  `5 * um`, `20 * nm`, `30 * deg`. They are ordinary names, so the grammar
  stays Python's.
- **Angles:** `sin`, `cos` and `tan` take an angle; a plain number given to
  them is in radians, as today.
- An expression whose dimension does not fit its field is an error:
  `rotation: width * 2` says "a length where an angle is wanted".

## Syntax

Python's expression grammar, restricted to:

- literals: whole and decimal numbers, `True`, `False`, lists `[a, b]`;
- names and dotted names (`mass.center.x`);
- arithmetic `+ - * / // % **` and unary `+ -`, with Python's meaning
  (`//` and `%` round towards minus infinity, as Python does); on lists only
  indexing and the functions below (`[1, 2] * 2` is an error, not Python's
  repetition);
- comparisons, chained as in Python (`0 < x < size`), and `and`, `or`, `not`;
- conditions: `a if condition else b`;
- calls of the language's functions;
- indexing `xs[i]`, and list comprehensions over finite lists:
  `[pitch * k for k in range(n)]`;
- assignment expressions for local names: `(r := width / 2)`.

Anything else Python allows (strings, dictionaries, lambdas, attribute access
on values other than points and layers, …) is a syntax error that says what is
not part of Sketch. `&` and `^` are for regions only, not Python's bitwise
operations on whole numbers.

## Operators on geometry

The same symbols as on numbers, chosen by type:

| | On regions |
|---|---|
| `a + b` | union |
| `a - b` | difference |
| `a & b` | intersection |
| `a ^ b` | exclusive or |

## Functions

What exists stays (`abs`, `min`, `max`, `round`, `sqrt`, `sin`, `cos`, `tan`,
`ceil`, `floor`). Added, in [steps](#steps):

- **Numbers and lists:** `clamp(x, lo, hi)`, `len`, `sum`, `all`, `any`,
  `range(n)`, `range(a, b, step)`; `min` and `max` of a list.
- **Points:** `distance(a, b)`, `point(x, y)`, `.x`, `.y`.
- **Regions:** a layer is a region (`level`, `layers.metal`); a shape's name
  is its region (`outline`); `sized(r, d)` (grow, or shrink with a negative
  `d`), `bbox(r)`, `hull(r)`.
- **Measurements** ([MEA-4](requirements.md#measurements)): `area(r)`,
  `perimeter(r)`, `centroid(r)`, `pieces(r)`.
- **Checks**, for rules: `width(r)`, `space(r)`, `space(a, b)`,
  `enclosure(outer, inner)`, `overlap(a, b)`, `touching(a, b)`. Each gives a
  measurement over the geometry; comparing it with a number gives the
  `violations` where the comparison fails: `width(device) < 2 * um` is the
  minimum width rule. `not touching(device, anchor)` gives the pieces of
  `device` that touch no anchor (the *anchored* rule).

Checks run on the geometry as the fab will see it: rounded onto the process's
grid, curves split within its tolerance ([DRC-3](requirements.md#rule-checks)).
The built-in rule kinds can then be written as Sketch rules shipped with the
standard library, so a fab's rule deck is data like everything else.

## Errors

**Before building**, as the text is typed, each with the span of text it is
about (the editor underlines it):

- syntax: a missing `)`, a stray operator, something Python has that Sketch
  does not;
- names: unknown, or a cycle through it;
- types and units: "a length where an angle is wanted", "`width` takes a
  region, not a number".

**While computing**, with the shape and place: division by zero, the square
root of a negative number, `level + 3` above the top of the stack, a list
index out of range, a list too long ([limits](#evaluation)).

Messages say what is wrong in the user's words and, where there is one, what
would be right.

## Evaluation

- Numbers are IEEE 754 doubles, computed as Python computes them (the engine's
  expressions already match Python bit for bit); regions are exact, in the
  geometry library.
- Values are computed when needed and cached by what they depend on.
- Limits keep it total: a list has at most 100,000 elements, and nothing is
  nested deeper than 64 levels.

## Where it lives

A library of its own, **`src/lang/`** (C++), owned by neither the engine nor the
editor:

```
src/geom      geometry library (regions, booleans, offsets, measurements)
src/lang      Sketch: parser, type checker, evaluator      (knows no geometry)
src/engine    builds components; plugs geom's operations into lang
mems_sketch   Python package; mems_sketch._lang for the editor
gui           asks _lang for types, errors with spans, completions
```

- **Parser:** Python's expression grammar as above, into a tree that keeps the
  span of every part. The engine's expression parser (`src/engine/src/expr/`)
  moves here and grows.
- **Type checker:** against an *environment* the caller gives: the names in
  scope with their types, and the functions with their signatures.
- **Evaluator:** values of the built-in types, and *opaque* types (regions,
  violations) whose operations the caller supplies. The language knows that
  `region - region` is a region; the engine says how to compute it, with the
  geometry library.
- **Python binding** (`mems_sketch._lang`): check an expression in an
  environment (type, errors with spans), complete a name at a position,
  evaluate. `mems_sketch.core.expressions` becomes a thin wrapper.
- The dependency order is `lang` → `engine` (which also uses `geom`) → the
  Python package → the GUI. The architecture test checks it; CI gets a `lang`
  area ([changed_areas.py](../../.github/scripts/changed_areas.py)).
- A **specification** (this document, made precise as it is built) and shared
  conformance tests: expressions with their value, type or error, run against
  the library and, while it exists, the Python evaluator.

## Files

Sketch lives inside a project's fields and needs no files of its own. The
extension **`.sk`** is reserved for when it does, for example rule decks
written as Sketch text.

## Compatibility

Every expression in a project today is valid Sketch with the same value: plain
numbers keep their field's unit, `sin` of a plain number is still in radians,
and layers written `level+1` in a layer field are Sketch layer expressions.
An expression that only worked by accident (a length added to an angle) becomes
an error that says so; the project is not changed until it is edited.

## Steps

1. **The library.** `src/lang/` with the parser and evaluator moved from the
   engine, the Python binding, and spans on every error. The editor's fields
   underline errors at their place.
2. **Types and units**, checked as fields are typed.
3. **Derived values and component checks** ([CMP-11](requirements.md#components-and-parameters), #64).
4. **Conditions and lists:** `enabled:` as a condition; lists, ranges and
   `i`/`j` wherever a value is.
5. **Regions:** layer algebra and shapes made from others.
6. **Measurements and checks:** design rules in Sketch; the built-in rule kinds
   rewritten as Sketch rules in the standard library.
7. **Rule decks** as `.sk` files, if wanted.

## Open questions

- An escape from units for formulas that are dimensionally odd on purpose
  (empirical fits): `value(x)` for a number without its unit?
- Do derived values belong only to components, or also to the process (derived
  constants)?
- Should layers by name always be written `layers.metal`, even in layer fields,
  for one rule instead of two?
