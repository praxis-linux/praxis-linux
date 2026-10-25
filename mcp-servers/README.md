# MCP Servers

First-party Model Context Protocol servers for Praxis.

## Servers

| Server | Purpose | Status |
|---|---|---|
| [`praxis-fs`](praxis-fs/README.md) | Files: list, search, read text and images, write text files, undo a write | Built (M0.8; writing with M0.11). Moving and deleting are not built. |
| [`praxis-run`](praxis-run/README.md) | Running one file the user picked (Python, shell, JavaScript) in a bubblewrap sandbox | Built (M0.11). Linux only. |
| `praxis-shell` | Sandboxed shell sessions | Planned |
| `praxis-browser` | Headless browser and web search | Planned |
| `praxis-settings` | System settings: audio, display, network | Planned (Phase 1) |

The model can ask `praxis-fs` for lookups. Writes, undos and runs are asked for only by the user's clicks in the Canvas; see the agent's [Code mode](../agent/README.md#code-mode).

## Structure

```
mcp-servers/
├── praxis-fs/
│   ├── src/praxis_fs/
│   │   ├── fs.py         # The rules: what may be read and written, and how
│   │   └── server.py     # Those rules as MCP tools over stdio
│   └── tests/
└── praxis-run/
    ├── src/praxis_run/
    │   ├── sandbox.py    # The bubblewrap sandbox a file runs in
    │   └── server.py     # runner_status and run_file over stdio
    └── tests/
```

All servers implement the [Model Context Protocol](https://modelcontextprotocol.io) and are written in Python. Each is its own project with its own lockfile; the agent depends on the ones it starts. `praxis-run` uses `praxis-fs`'s path rules, so what may not be read may not be run either.
