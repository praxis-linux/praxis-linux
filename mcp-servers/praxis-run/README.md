# praxis-run

The Praxis code-running MCP server. It runs one file the user picked, inside a [bubblewrap](https://github.com/containers/bubblewrap) sandbox, and returns what the file printed. It is a separate server from [`praxis-fs`](../praxis-fs/README.md), so reading files and running code never share a process.

The agent calls it only when the user presses **Run**. The model cannot run anything.

## Tools

| Tool | Arguments | Returns |
|---|---|---|
| `runner_status` | — | Whether files can be run here, why not if they cannot, and which kinds (`.py`, `.sh`, `.js`). |
| `run_file` | `path` | The exit code, what the file printed on stdout and stderr, whether it was stopped for taking too long, and how long it took. |

Paths follow the same rules as in `praxis-fs`: inside the allowed folders, nothing hidden. A Python file runs with `python3`, a shell file with `bash` and a JavaScript file with `node`, if they are installed in a system folder (`/usr` or `/opt`).

## The sandbox

The file runs with:

- **No network.** Every namespace is new, the network one included.
- **Read-only system folders**: `/usr`, `/opt` and the `/bin` and `/lib` links into `/usr`. From `/etc`, only what programs need to start: the dynamic linker's cache and configuration, `passwd`, `group`, `nsswitch.conf`, `localtime` and `alternatives`.
- **Its own folder, which it can read but never change.** Where bubblewrap can lay a throwaway layer over it (`--tmp-overlay`, bubblewrap 0.10 and later, e.g. on Arch), the code may write there too, and what it writes is gone when it ends; with older bubblewrap (Ubuntu 24.04 has 0.9) writes there fail. Either way your files stay as they were: code that could write to its folder could plant a git hook or `.envrc` that runs outside the sandbox later.
- **Masked in that folder, at any depth**: hidden files and folders (`.env`, `.git`, …), which `praxis-fs` would not read either, and sockets, which reach programs outside the sandbox. A folder with more than 20,000 entries to check is not run in.
- **A private, empty `/tmp`**, where it may write, and home.
- **No access to** the rest of your home folder, `/mnt` and `/media` (on WSL that includes the Windows drives), `/run` (and with it your session and D-Bus), `/var`, or other processes; and no user namespaces of its own.
- **Limits**: stopped after 10 seconds; output cut off after 64 KB each for stdout and stderr; files it writes may not exceed 16 MB; at most 256 open files.

Not limited yet: memory, and how many processes the code starts. Those need cgroups, which come with the Phase 1 session.

The system folders are shown whole, so running code is turned off when an allowed folder or the home folder is inside one of them (`PRAXIS_FS_ROOTS=/opt/work`, say) or is `/`: `runner_status` says so.

An interpreter is used only if both its name on `PATH` and the file it points to are in the system folders, and only if it starts inside the sandbox: a `python3` in a virtual environment, or a snap's `node`, is not.

When running as root (the default user in WSL), the code also runs as root inside the sandbox. It still sees only what is listed above.

## Requirements

Linux and bubblewrap (`sudo apt install bubblewrap`, `sudo pacman -S bubblewrap`). Some systems restrict the user namespaces bubblewrap needs; `runner_status` then says that it could not start a sandbox, and why. On Windows and macOS, `runner_status` reports that running code needs Linux, and the Canvas shows no Run button.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PRAXIS_FS_ROOTS` | the home folder | Folders whose files may be run, as for `praxis-fs`. |
| `PRAXIS_FS_ALLOW_HIDDEN` | unset | Set to `1` to allow running hidden files, and to leave hidden entries unmasked. |

## Run

The agent starts this server itself, over stdio. To run it alone:

```bash
cd mcp-servers/praxis-run
uv sync
uv run praxis-run
```

## Tests

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

The tests that run code inside the sandbox are skipped where bubblewrap cannot start one, such as on Windows.
