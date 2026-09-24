# mems-sketch

Parametric MEMS layout design. A design is built from components whose
values are parameters and expressions, so changing a pitch or a gap redraws
everything that depends on it. The tool checks design rules and exports GDS,
OASIS and DXF. It is for sketching a design: process steps after it (etch
compensation, mask preparation) and simulation are out of scope.

![The editor with the resonator example](docs/images/window.png)

- **Parametric all the way down:** every value can be an expression; parts
  stay attached through alignment points when values change.
- **Non-destructive operations:** subtract, offset, fillet, arrays, mirrors
  and per-corner rounding stay editable.
- **Projects are text:** a folder of small YAML files, made for git, with a
  History panel that shows what changed, in words and on the canvas.
- **Works with other tools:** GDS and OASIS import; projects and geometry as
  JSON, XML or MATLAB `.mat`; a command line and a Python API.

## Install and run

```bash
pip install -e ".[gui]"                        # add ,matlab for .mat files
mems-sketch examples/resonator/project.yaml    # the editor
mems-sketch-cli check examples/resonator       # the same design, from the command line
```

## Documentation

- **[User guide](docs/user/README.md)**: the editor, components and
  parameters, shapes and operations, files and formats, history.
- **[Developer guide](docs/developer/README.md)**: architecture, the Python
  API, file formats, and how to extend the tool.
- **[Contributing](CONTRIBUTING.md)**: branches, pull requests and checks.
