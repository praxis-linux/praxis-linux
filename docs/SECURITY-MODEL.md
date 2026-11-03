# Security model

What Praxis's agent can and cannot do, where the boundaries are, and how to check what it did. This describes Phase 0, as it is in this repository. It has had an internal review but no outside audit. To report a problem, see [SECURITY.md](../SECURITY.md).

## In short

- The model can read files inside the folders you allow, and propose changes. It cannot write a file or run anything.
- Only your click writes a file or runs code. What you apply is exactly what the diff showed.
- Code you run has no network and cannot change your files. The agent itself is **not** sandboxed.
- With a local model, nothing leaves the machine through Praxis's own requests. With a cloud model, what the model sees goes to the provider.
- Writes and runs are logged before they happen, and a change can be looked at and undone for 30 days, until the file changes again.

## Who can do what

| Action | The model | Your click | Enforced by |
|---|---|---|---|
| List, search and read text files | Yes, up to 3 lookups a turn | Yes | The model is only offered these steps |
| Read an image | No | Yes | Not among the model's steps |
| Propose a change or a new file | Yes | | A proposal only draws a diff; nothing is written |
| Write a file (Save, Apply) | **No** | Yes | The agent only calls the write tool from these clicks |
| Undo a change | **No** | Yes | Same |
| Run a file | **No** | Yes | Same |
| See the record of changes | **No** | Yes | Not among the model's steps, and not added to its prompt |
| Move, rename or delete a file | No | No | No such tool exists. Undoing a new file removes it, which is the one exception |

This does not rely on the prompt. The model's answers are parsed into a fixed set of steps, and buttons on the Canvas come from the agent's code. A model can write a card or a form, whose click only sends it text; it cannot draw Save, Apply, Undo or Run, or see the ids they carry.

## Boundaries

**Files** ([`praxis-fs`](../mcp-servers/praxis-fs/README.md)). Reads and writes stay inside the allowed folders (`PRAXIS_FS_ROOTS`, by default the home folder), after following links. Hidden files and folders are refused at any depth. A write needs the hash the file had when it was read, so a file changed since is not overwritten, and a new file never replaces one that appeared meanwhile.

**Running code** ([`praxis-run`](../mcp-servers/praxis-run/README.md)). A file you run is in a bubblewrap sandbox:

| It can | It cannot |
|---|---|
| Read the system folders (`/usr`, `/opt`) and a few files from `/etc` | Reach the network |
| Read its own folder, except hidden files and sockets at any depth | Change your files: its folder is read-only, or with bubblewrap 0.10 and later, writes there vanish when it ends |
| Write to a private, empty `/tmp` | See the rest of your home folder, `/mnt`, `/media`, `/run`, `/var`, or other processes |
| Run for 10 seconds, and print 64 KB each to stdout and stderr | Create user namespaces of its own |

Running is turned off if an allowed folder or the home folder is inside a folder the sandbox shows whole. It needs Linux and bubblewrap; otherwise there is no Run button.

**The agent's port.** The agent listens on 127.0.0.1 only and refuses other host names and web origins. The AppImage starts it with a random token that only its window knows, so other programs on the machine cannot use it. An agent started by hand without `PRAXIS_AGENT_TOKEN` answers any program on the machine, and in `scripts/dev.py --web` the token can be fetched from the dev server.

**The Canvas.** It renders only a vetted catalog of components, none of which load remote content. Links and images the model writes are shown as text. The desktop window refuses to navigate away.

**Not sandboxed.** The Canvas, the agent, the model proxy and the file server run as you, with your permissions. The boundaries above limit what they are asked to do. They would not contain a bug in them, or in their dependencies.

## What leaves the machine

The model alias decides this (`PRAXIS_MODEL`). An alias counts as local only if its name starts with `local-`, so an unknown one is treated as cloud.

**Local alias:** requests go to Ollama, on this machine by default.

**Cloud alias:** each turn sends the provider:
- your request, and the last ten turns of the conversation;
- names and text of the files the model looks up (up to 4,000 characters per file);
- which file or folder is on screen;
- after a Run, the exit code and the last 1,500 characters each of the output and errors;
- the text of any change it proposes.

Each cloud request is announced before it is sent and recorded in the audit log. There is no per-file consent yet. Your own edits and the Changes screen are not sent, unless the model then reads that file.

We have not audited the network behaviour of the LiteLLM proxy itself. Ollama and the proxy are outside this boundary.

## Audit and rollback

**The audit log** (`~/.praxis/audit.db`) gets a row before each model call and tool call; if the row cannot be written, the call is not made. It holds no prompts and no file text. For each write it records:

| | |
|---|---|
| Where it came from | `origin`: `save` (your text) or `apply` (a model's), and `proposed_by`, the model alias |
| Who asked | `asked_by`: `user` |
| What was checked | the path, the number of characters and the expected hash |
| What happened | the status, the new hash, and the backup id |

```bash
sqlite3 ~/.praxis/audit.db "SELECT started_at, tool, asked_by, status, arguments, result
  FROM tool_calls WHERE tool IN ('write_text_file', 'undo_write', 'run_file')"
```

**The record of changes** (`~/.praxis/backups/<backup id>/`, mode `0700`) keeps the text before and after each write, for 30 days, then removes it. The **Changes** button in the Canvas lists these with their diffs. `write.json` in each folder has the same details. An audit row outlives its backup: after 30 days the hashes remain, but not the diff.

**Undo** restores one file, from the Changes screen or right after the change. It is refused if the file has changed since, so it never overwrites later work.

## Known limits

- **Rollback is per file.** There is no undoing several changes together, or anything outside Praxis's own writes. Btrfs snapshots before agent actions are planned ([M1.6](ROADMAP.md)).
- **The audit log is not tamper-proof.** It is a local SQLite file you can edit, and anything running as you can too. There is no signing, and no export to a central log.
- **Text stays on disk.** The conversation store holds what the model read, until you delete the conversation. Backups hold old file text for 30 days, secrets included. Neither is encrypted by Praxis, and deleting a conversation does not remove backups.
- **Prompt injection is bounded, not prevented.** A file the model reads can steer it. It can then read more files (which reach the provider, with a cloud alias), mislead you in its text, or propose a harmful change. Read the diff before you apply it: an applied change that you then run is code the model wrote.
- **The sandbox has no memory or process limits.** Code can exhaust either for up to 10 seconds. Those need cgroups, planned for the Phase 1 session.
- **Run output reaches the model**, and so a cloud provider with a cloud alias. A file you run can print anything its sandbox can read.
- **As root, sandboxed code runs as root** (the default user in WSL). It still sees only what is listed above.
- **Single user, single machine.** There are no accounts, roles or policy controls yet.
