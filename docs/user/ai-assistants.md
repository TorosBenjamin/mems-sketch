# AI assistants (MCP)

An AI assistant can work on a design the way you do in the editor: open a
project, read and change components, build them, look at them, check them
against the process's rules and export them. It does this through an
[MCP](https://modelcontextprotocol.io) server that comes with mems-sketch:

```bash
pip install "mems-sketch[gui,mcp]"     # gui: for pictures; mcp: the server
mems-sketch-cli mcp path/to/project    # the server, on stdin/stdout
```

You don't run the server yourself: the assistant's app starts it when it
needs it.

## Setting it up

**Claude Code**, in a terminal in the folder you work in:

```bash
claude mcp add mems-sketch -- mems-sketch-cli mcp path/to/project
```

**Claude Desktop, Cursor and other MCP clients** take the same command in
their MCP settings (for Claude Desktop, `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "mems-sketch": {
      "command": "mems-sketch-cli",
      "args": ["mcp", "/path/to/project"]
    }
  }
}
```

Leave out the project and the assistant starts on a new, empty one (it can
open or create one itself). If `mems-sketch-cli` is not on the app's `PATH`,
give its full path (in the virtual environment it was installed in:
`.venv/bin/mems-sketch-cli`, on Windows `.venv\Scripts\mems-sketch-cli.exe`).

## What it can do

| Tools | What for |
|---|---|
| `open_project`, `new_project`, `save`, `undo`, `redo`, `project_info` | The project |
| `list_components`, `get_component`, `describe_kinds` | Reading: the project's and the libraries' components, a component as YAML (the file format), every shape kind and modifier with its fields |
| `add_shape`, `update_shape`, `rename_shape`, `delete_shape`, `add_modifier`, `set_parameter`, `set_point`, `place_component`, `set_component_yaml` | Editing, by name, in the file format |
| `build` | Sizes: the bounding box, each layer's area and polygon count, the points and ports, for trial parameter values |
| `render`, `render_variants` | Pictures: a component in the canvas's colours (points marked, zoomed in), or a parameter sweep side by side at one scale |
| `check` | The process's design rules, with an optional picture marking the violations |
| `export` | GDS, OASIS, DXF (or JSON, XML, MAT) |

Edits go through the same edit session as the editor:

- **An edit that breaks the design is refused.** If a project built before a
  change and doesn't after, the change is undone and the assistant gets the
  reason, the message the editor would show.
- **Every edit can be undone** with `undo`.
- **Library components are read-only.** The assistant can read and place
  them, but not change them.

Nothing is written to disk until the assistant calls `save` (or `export`).

## Working together

The server keeps its own copy of the project. If the editor has the same
project open, it doesn't see the assistant's changes until they are saved
and you reopen the project, and the assistant doesn't see yours until you
save. Let one of you edit at a time, and save before handing over. A live
link to a running editor is planned.
