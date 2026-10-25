# praxis-fs

The Praxis filesystem MCP server. Read-only: it can list a folder, find files by name and read a text file. Nothing in it can create, change, move or delete anything.

## Tools

| Tool | Arguments | Returns |
|---|---|---|
| `allowed_folders` | — | The folders the server may read. The first is the main folder. |
| `list_directory` | `path` | What is directly inside one folder, folders first. |
| `search_files` | `pattern`, `directory`, `modified_within_days` | Files under a folder at any depth whose name matches, newest first. |
| `read_text_file` | `path`, `max_bytes` | The text of one file, cut off at `max_bytes` (at most 64,000). |

Paths may be absolute, start with `~`, or be relative to the main folder. An empty path is the main folder.

`pattern` is a file-name pattern such as `*.py`. It is read generously, because the callers are often small language models: several patterns may be separated by commas, bars or spaces; a bare extension (`.pdf`) means `*.pdf`; a plain word matches any name containing it.

## What it will not read

- **Anything outside the allowed folders.** Every path is resolved, following symlinks and junctions, and must land inside an allowed folder. A link that points outside is neither followed nor listed.
- **Hidden files and folders**: any name starting with a dot, which covers `.ssh`, `.env`, `.git` and Praxis's own `.praxis`; and on Windows anything with the hidden or system attribute, which covers `AppData`. They are not listed, not searched and not readable by exact path.
- **Alternate data streams** on Windows (`notes.txt:stream`), a second file hidden inside a visible one.
- **Binary files.**
- `node_modules` and `__pycache__` are skipped when searching.

A refusal is returned as a tool error whose text starts with `Refused: `, so a caller can tell "not allowed" from "not there".

Listings stop at 200 entries. A search does not follow links into folders, so a link that loops back cannot keep it going; it stops after 50,000 files and folders or 10 seconds, whichever comes first. Both say so in the result when they were cut short.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PRAXIS_FS_ROOTS` | the home folder | Allowed folders, separated by `:` (`;` on Windows). The first is the main folder. |
| `PRAXIS_FS_ALLOW_HIDDEN` | unset | Set to `1` to list and read hidden files. |

## Run

The agent starts this server itself, over stdio. To run it alone:

```bash
cd mcp-servers/praxis-fs
uv sync
uv run praxis-fs
```

## Tests

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## Not here yet

Writing, moving and deleting. They wait for the consent prompt and the undo story (M0.12, and Btrfs snapshots in Phase 1): an agent that can change files has to be able to show what it will do and take it back.
