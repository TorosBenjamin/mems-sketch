# std: the bundled generic library

Every project can use it as `std.` without declaring a path. Its components
are the parts every microfabrication layout uses, whatever the device:
alignment marks, test structures, pads and a die frame.

They work in any process: they draw on the level they are placed on
(`level`) and, for a second layer, the level above it (`level+1`), so a cross
mark placed on one level has its counterpart on the next. A component that
needs a role (the release test's `level.anchor`) says so in its description.
