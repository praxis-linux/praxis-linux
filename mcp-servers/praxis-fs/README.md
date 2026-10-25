# praxis-fs

The Praxis filesystem MCP server. It can list a folder, find files by name, read a text file or an image, and write a text file. It cannot move, rename or delete anything.

It does not ask the user anything: the agent only calls the write tools when the user presses Save or Apply in the Canvas, and the model cannot call them. See the agent's [Code mode](../../agent/README.md#code-mode).

## Tools

| Tool | Arguments | Returns |
|---|---|---|
| `allowed_folders` | — | The folders the server may read. The first is the main folder. |
| `list_directory` | `path` | What is directly inside one folder, folders first. |
| `search_files` | `pattern`, `directory`, `modified_within_days` | Files under a folder at any depth whose name matches, newest first. |
| `read_text_file` | `path`, `max_bytes` | The text of one file, cut off at `max_bytes` (at most 64,000). When the whole file was read and is UTF-8, also its `sha256`, which writing it back needs. |
| `read_image` | `path` | A PNG, JPEG, GIF or WebP image, base64-encoded, up to 8 MB. Judged by the file's bytes, not its name; SVG is never read as an image, since it can carry script. |
| `write_text_file` | `path`, `content`, `expected_sha256` | Writes a text file, up to 1 MB. To change a file, `expected_sha256` must be the hash it was read with; without it, only a new file is created. Returns the new hash and a `backup` id. |
| `undo_write` | `backup` | Puts the file back as it was before that write (or removes a file the write created), unless it has changed since. Each backup undoes once. |

Paths may be absolute, start with `~`, or be relative to the main folder. An empty path is the main folder.

`pattern` is a file-name pattern such as `*.py`. It is read generously, because the callers are often small language models: several patterns may be separated by commas, bars or spaces; a bare extension (`.pdf`) means `*.pdf`; a plain word matches any name containing it.

## Writing

- **Never blind.** A change needs the hash the file had when it was read, so a file changed since (by the user, or another program) is not overwritten. A new file is only created where none exists, in a folder that already exists.
- **Always undoable.** Before each write, what it replaces is kept in `~/.praxis/backups/<id>/`, which `praxis-fs` will not read. Nothing removes old backups yet.
- **Whole or not at all.** The new text is written to a temporary file beside the old one, which then replaces it in one step. A file that used CRLF line endings keeps them, and an executable stays executable.
- **The same rules as reading**: inside the allowed folders, nothing hidden.

## What it will not read or write

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
| `PRAXIS_FS_ALLOW_HIDDEN` | unset | Set to `1` to list, read and write hidden files. |
| `PRAXIS_FS_BACKUPS` | `~/.praxis/backups` | Where writes keep what they replaced. |

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

Moving, renaming and deleting. Like writing, each needs a way to show the user what will happen and to take it back; for whole folders that means Btrfs snapshots, in Phase 1.
