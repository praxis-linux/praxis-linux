# ADR-003: UI wire format, catalog, and the agent–Canvas contract

**Status:** Accepted
**Date:** 2026-09-30 (sections 8 and 9 added the same day, with M0.8; sections 10 and 11 on 2026-10-25, with M0.9; section 12 on 2026-10-25, with M0.11)
**Decision-makers:** Founding contributor

## Context

[ADR-001](./ADR-001-strategic-positioning.md) §5 chose declarative generative UI with **A2UI v0.9** as the wire format. The M0.5 scaffold did not implement it: the Canvas rendered a homegrown `{mode, title, body}` payload with three leaf components, no component ids, no containers, no way for the UI to send anything back, and no validation of what the agent sent.

Two things had to be settled before the agent service (M0.6) could be built on top:

1. Whether to keep a schema of our own or render real A2UI.
2. Who writes the UI: the model directly, or code on the model's behalf.

When M0.5 landed there was no official React renderer. There is now: `@a2ui/react` (Apache-2.0, supports React 19) with a v0.9 implementation that provides Zod-validated catalogs, containers, data binding and an action channel.

## Decision

### 1. The Canvas renders real A2UI v0.9 with the official renderer

`@a2ui/react/v0_9` renders surfaces; `MessageProcessor` from `@a2ui/web_core` holds the state. The homegrown renderer and types are removed. The processor runs with **strict validation**: a component outside the catalog, a dangling reference or a tree without a root is rejected and reported, not partially rendered.

### 2. The Praxis catalog is a vetted subset of the A2UI basic catalog

Catalog id: `praxis-linux:catalog:phase0`.

| Included | Left out | Why |
|---|---|---|
| Text, Row, Column, List, Card, Tabs, Divider, Modal, Button, TextField, CheckBox, ChoicePicker, Slider, DateTimeInput | Image, Video, AudioPlayer, Icon | They load remote URLs, which would let a surface make network requests |
| FileImage (Praxis's own) | | Shows an image file from an inline `data:image/…;base64,…` address only (PNG, JPEG, GIF or WebP); anything else is refused by its schema, so it cannot fetch |
| CodeEditor (Praxis's own) | | A CodeMirror 6 editor for a file's text. Its `value` is bound to the data model, so what the user types is what Save sends (section 12). It loads nothing |
| All basic functions except one | `openUrl` | A surface must not be able to navigate away |

`Button` is our own implementation of the basic catalog's Button API so the Canvas can style each variant. Praxis-specific components (`FileTree`, `CodeEditor`, …) join this catalog as File and Code mode land.

### 3. The model fills in a UI plan; code compiles it to A2UI

The model never writes A2UI. It returns a small nested structure — the **UI plan**: a mode, a title, and a list of blocks (`text`, `code`, `cards`, `form`). Deterministic builders in `agent/src/praxis_agent/ui/` compile the plan into A2UI messages.

Everything the protocol requires structurally is therefore guaranteed by code, not asked of the model: unique ids, a `root`, references that resolve, bindings that exist in the data model, and bounds on how much one reply can put on screen.

### 4. Structured output is schema-constrained

The planner asks the provider for JSON constrained to the plan's schema (PydanticAI `NativeOutput`), not for a tool call. With `qwen2.5:7b` through LiteLLM and Ollama, tool-call output failed on a code request after two retries; constrained output has produced a schema-valid plan on every call since.

### 5. Transport: NDJSON over localhost HTTP

| Endpoint | Body | Meaning |
|---|---|---|
| `POST /intent` | `{"intent": "..."}` | The user typed a request |
| `POST /action` | An A2UI v0.9 client-to-server `action` message | The user clicked something on a surface (section 7) |
| `POST /resume` | — | The Canvas opened: show where this conversation left off, or nothing if it is new |
| `POST /stop` | — | The kill switch: end every turn the agent is working on, in every conversation. Answers `{"stopped": n}` |
| `POST /forget` | — | Delete this conversation and what was read in it. Answers `{"forgotten": n}`; the audit log keeps its records |

All require an `X-Praxis-Session` header, which names the conversation, and, when the agent was started with a token, an `X-Praxis-Token` header carrying it (HTTP 401 otherwise). They answer with one JSON event per line:

| Event | Payload | When |
|---|---|---|
| `call` | `model`, `destination` (`local` or `cloud`) | Before the model is called, after the audit row is written |
| `status` | `text`, e.g. "Searching for *.py…" | Before each lookup runs, and once the model has started writing a proposed change |
| `mode` | `mode` | With the first drawing of a screen, and whenever it changes |
| `a2ui` | `message` — one A2UI server-to-client message | Once per message |
| `error` | `message` | The turn failed |

### 6. Two surfaces: the workspace and the trail

`workspace` holds the screen for the current turn. `context`, shown in the context rail, holds the conversation trail: one row per turn, the one on screen marked.

The first drawing of a screen replaces the surface (`deleteSurface`, `createSurface`, `updateComponents`, and `updateDataModel` when there is a form). Deleting a surface that does not exist is a no-op, so the agent does not track what the Canvas is showing. While a reply streams in (section 10), later drawings send only `updateComponents`, so the screen grows without flicker, unless that would leave a component unreachable from the root, which the Canvas rejects; then the surface is replaced again. In place, only the forms that are new are sent, each at its own path (`/b1`), so streaming never wipes what the user has typed into a form.

Component ids are stable for that reason: the model's blocks are `b0`, `b1`, …, and what the agent put there itself (a listing, a file, an image, an editor, a proposed change, what a run printed) is `f0`, `f1`, …, so a listing keeps its id while the reply around it is still being written. An editor's text lives in the data model at `/f<n>/content`.

### 7. UI actions

| Action | Context | Agent treats it as |
|---|---|---|
| `intent` | `text` | A new request with that text |
| `submit` | `purpose`, `values` (the form's data) | A request carrying the user's answers |
| `open` | `target`, `kind` (`file` or `directory`) | A click on a file or folder: the agent calls the filesystem tool directly and shows the result. No model is called. |
| `revisit` | `turn` | A click on a turn in the trail: the agent shows that turn's screen again as it was stored. No model and no tool is called. |
| `edit` | `target` | Open the file in the editor |
| `save` | `target`, `base` (the file's hash when it was opened), `content` (bound to the editor) | Write what is in the editor, unless the file has changed since |
| `run` | `target` | Run the file as saved, in the sandbox, and show what it printed |
| `apply` | `proposal` | Make the change the model proposed, unless the file has changed since |
| `discard` | `proposal` | Drop the proposed change |
| `undo` | `backup` | Put the file back as it was before a save or an applied change |

The last six never call a model (section 12). Their context is checked before anything is streamed back, and a malformed one is rejected with HTTP 400. Any other action name is rejected without calling a model.

A context key must not be named `path` or `call`: A2UI reads an object with such a key as a data binding or a function call, and the context reaches the agent empty. That is why `open` carries `target`.

### 8. The model asks for tools through the same constrained output

Each model step is one schema-constrained object: a lookup (`search_files`, `recent_files`, `list_directory`, `read_text_file`), a proposed change (`propose_edit`: a path, a one-sentence summary and the file's complete new text), or the reply. The agent runs the lookup against the MCP server, tells the model the result, and asks for the next step. A proposed change ends the turn (section 12). After three lookups only a reply is accepted.

Provider function calling is not used. With `qwen2.5:7b` through LiteLLM and Ollama it failed both ways it was tried: alongside constrained output the model never called a tool (0 of 8 turns) and announced files it had not looked up; without constrained output it looped (26 requests in one turn) or called tools that do not exist (7 of 8 turns failed).

Lookups have no optional fields. A small model fills every field it is offered: an optional "changed in the last N days" on the search was set on almost every search, often to 0, and turned plain searches into empty ones. Searching by name and finding recent files are separate lookups instead.

### 9. What was looked up is shown by the agent, not retold by the model

A listing or a file's text goes on the screen straight from the tool result, placed after the model's answer. The model is asked for a sentence about it. The same holds for refusals: when the filesystem server refuses a lookup (a hidden file, a path outside the allowed folders), the screen says so in the agent's words.

This is what makes file names and contents on the Canvas trustworthy with a model that is not: what the model *says* can still be wrong, and the evidence below shows it sometimes is.

### 10. Replies are drawn while they are written

The planner streams each model step with PydanticAI's `iter()`, parses the partial JSON as it arrives, and hands the agent a draft of the screen at most every 0.1 s once the step turns out to be a reply. `run_stream()` is not used: it cannot retry a reply that fails validation, and the planner relies on retries.

A draft shows text and code as they grow. Cards and forms appear only once whole, because a half-written form would change its field names, and so its data bindings, as it streams. Lookups are never drawn from partial output; the agent waits for the whole step and sends a `status` event before running it.

### 11. Conversations are stored on the machine

Every finished turn is stored in SQLite (`~/.praxis/conversations.db`): what the user asked, the screen that was drawn, what was on it, and every message the model was sent and answered, lookups included.

- **The model is shown the last ten turns**: the latest in full, so it can refer to what it just found; the earlier ones as the request and the final reply only, so file contents read in earlier turns are not sent again.
- **What the user is looking at is added to the prompt.** When the screen shows a file or a folder, from a reply, a click or a revisited turn, the request goes to the model followed by "(On the user's screen right now: the file docs/plan.md.)". This is what lets "summarise this" work.
- **The Canvas keeps the conversation id across reloads**, and asks `/resume` on start to show the last screen and the trail again. "New" starts another conversation; the old one stays stored.
- Resuming and revisiting replay stored A2UI messages. Neither calls a model or a tool.

### 12. Files change and code runs only when the user clicks

The model can propose; only the user's click acts. That is enforced by what each side can call, not by the prompt:

- **The model's only way to change a file is `propose_edit`.** The agent reads the file as it is now, shows the change as a diff with Apply and Discard, and keeps the proposed text itself, under a random id, until the user decides. The Canvas only ever sends that id back, so what is applied is exactly what was shown. Proposals are kept in memory: up to 20 per conversation, and none after the agent restarts. "Delete…" drops them too.
- **A proposal is refused, with the reason on screen, for** a hidden file or one outside the allowed folders, a file that is not whole UTF-8 text, and a file longer than the 4,000 characters the model is shown of a file, since a change to the part it did not see would lose it. A file keeps its final line break, which small models tend to drop.
- **Every write checks the file's hash.** `edit` reads the whole file (up to 64 KB) and its hash; `save` and `apply` pass that hash to `praxis-fs`, which refuses the write if the file has changed since. A new file is only created where none exists.
- **Every write can be undone.** `praxis-fs` keeps what each write replaced in `~/.praxis/backups`, and `undo` restores it, unless the file has changed again since.
- **Running is a separate server, `praxis-run`, and only the user's Run asks for it.** The file runs in a bubblewrap sandbox: no network, the system folders read-only, a private `/tmp`, its own folder readable but never changed (where bubblewrap has overlays, writes there go to a layer thrown away at the end), hidden files and sockets in it masked at any depth, stopped after 10 seconds. Where that cannot work (Windows, macOS, no bubblewrap), the Canvas shows no Run button. What a run printed is added to the next prompt, so "why did that fail?" works.
- **What the diff shows is what is written.** Lines are split at `\n` only, as interpreters split them; invisible and control characters (other line breaks, zero-width and bidirectional marks) are written out as `⟨U+XXXX⟩` with a warning, trailing whitespace is shown, and long lines wrap. Only the agent's own diffs are coloured as proposed changes: a `praxis-diff` fence, which the agent strips from the model's code.
- **Every write and run is audited before it happens**, as asked for by the user. For writes the audit log records the path, how many characters and the expected hash, never the text; for an undo, which file it put back.
- **The kill switch stops file actions too**, as well as model turns.

## Evidence

Thirty planner calls per model (ten intents, three runs each) through LiteLLM and Ollama, on the instructions in `planner.py` as of this ADR:

| | qwen2.5 7B | qwen2.5 14B |
|---|---|---|
| Schema-valid plans | 30 / 30 | 30 / 30 |
| Replies with no blocks at all | 1 | 0 |
| File and shell requests (12) answered with a refusal or a how-to, not invented results | 12 | 12 |
| Used a form when asked for one | 0 / 3 | 3 / 3 |
| Replies that offered cards | 3 | 10 |
| Time per turn | 1–5 s | 1.5–10 s |

The plumbing is reliable with a 7B model. Using the UI well is not: the 7B model answers mostly in text and seldom reaches for a form or cards. Worked examples in the instructions made it worse — replies became as short as the examples and code requests lost their code. An earlier, vaguer set of instructions had the 7B model inventing file listings and command output on most file and shell requests; stating plainly what the agent cannot do yet stopped that in these runs. The file and shell row was judged from the opening of each reply, not a full read.

### With file lookups (M0.8)

Thirty-six turns on `qwen2.5:7b` (twelve intents, three runs each) against a sample folder, through the real `praxis-fs` server:

| | Result |
|---|---|
| Turns that ended in a valid screen | 36 / 36 |
| Tool calls | 64: 57 succeeded, 4 refused (a hidden file three times, a path outside the folder once), 3 failed on a wrong path |
| Requests to find, list or read (21 turns): the right file or folder was on screen | 21 (one "summarise my report" found the report but used up its lookups before reading it) |
| General questions (6 turns) answered without touching a file | 6 |
| Hidden file (3 turns): refused, and the refusal shown | 3, while the model's own text said the file did not exist in 2 |
| Asked to delete (3 turns): said it cannot | 2 |
| Asked to rename (3 turns): said it cannot | 0 — two replies offered to rename the file |
| Model requests per turn | 2.8 on average; 1.2–4.7 s per turn |

Nothing was changed in any run, because nothing can be: the server has no tool that writes. The model's claims about what it can do are the unreliable part, not the files it shows.

### With streaming and memory (M0.9)

Twelve scripted conversations on `qwen2.5:7b` (four scripts, three runs each: 24 model turns and 3 files opened by clicking), plus one run in a real browser against the production build:

| | Result |
|---|---|
| Follow-ups that depend on an earlier turn ("how is that different…", "what was the first thing I asked?", "what does the first one do?", "what are the action items in it?") | 12 / 12 answered about the right thing |
| "Summarise this" after opening a file by clicking | 3 / 3 read and summarised that file |
| A follow-up after reloading the Canvas, in the browser | Answered from the conversation before the reload |
| Time to the first drawing, model warm | 0.6–2.2 s; the whole reply 0.8–6 s |
| Drawings per long reply | 13–41 |
| Turns where the same folder was listed twice | 1 of 24, now shown once |

The first request after the model has been idle is slow: 5.8 s to the first drawing in these runs, and 42 s when Ollama had to load the model after a restart.

### With Code mode (M0.11)

The same script on Ubuntu 24.04 (WSL) with the real `praxis-fs` and `praxis-run`, once on Claude Haiku 5.5 (`cloud-fast`) and once on `qwen2.5:7b`, then again in a real browser against the Canvas on Claude Haiku: open a file, edit, save, run, undo; ask for a change to the file; ask for a new file; run both.

| | Claude Haiku 5.5 | qwen2.5 7B |
|---|---|---|
| Edit, save, run, undo (no model) | Worked | Worked |
| "Make greet put an exclamation mark at the end": read the file, then proposed the change | One-line diff, correct; ran as expected after Apply | Same, but dropped the file's final line break (now kept by the agent) |
| "Create demo/hello.py that prints 1 to 3": proposed a new file | Correct; ran after Apply | Correct; ran after Apply |
| The sandboxed file tried the network and looked for `~/.praxis` | Network unreachable; not visible | Same |

In the browser run, the user typed a line into the file, saved it, then asked for a change. The model proposed the change from its read of the file in an earlier turn, and the diff showed it would delete the user's new line. Nothing was applied. The instructions now say to read the file again in the same turn before proposing; on the rerun the model did, and kept the line. This is the case the diff exists for, and why the agent never applies a change itself.

## Consequences

**Positive:**
- The Canvas is what ADR-001 said it would be: standards-aligned, and we inherit the renderer instead of maintaining one.
- The catalog is an enforced allowlist, which is the security argument for declarative UI made real.
- A small local model can drive the Canvas without ever producing an invalid screen.
- The contract is tested from both sides: the agent's builders write a golden file, and the Canvas tests feed that file to the real renderer.

**Negative / risks:**
- **Files can carry instructions to the model (prompt injection).** A file the model reads can steer it: make it read up to three more files per turn, and with a cloud alias those go to the provider; write misleading text or code ("run `curl … | sh`"); offer a card whose request differs from its title; propose a harmful change to a file; and stay in the conversation's history. Links and images it writes are shown as text, never followed or loaded. A proposed change is only a diff until the user applies it, and can be undone; but a user who applies without reading the diff, then presses Run, runs what the model wrote. The sandbox bounds that run (no network, nothing of the user's changed, 10 seconds) but does not yet limit memory or the number of processes. What a run prints goes into the next prompt, so with a cloud alias it reaches the provider. A per-turn consent for cloud calls and showing a card's request before it runs are the next defences.
- **Running code is Linux-only and needs bubblewrap.** Where bubblewrap cannot make user namespaces (some hardened kernels, Ubuntu's AppArmor restriction for programs without a profile), there is no Run button and the reason is logged.
- **The token is only as private as where it is kept.** Host and Origin checks stop web pages, including DNS rebinding. Other programs on the machine are stopped by `PRAXIS_AGENT_TOKEN`, which the packaged app makes for each start and keeps in its own window; `scripts/dev.py` makes one too, but in browser mode it sits in the page the dev server serves. An agent started by hand without the variable answers anyone on the machine.
- **Conversations hold whatever was read during them**, file contents included, in `~/.praxis/conversations.db`. It is on this machine and `praxis-fs` will not read it. "Delete…" removes a conversation (and compacts the file so the text is gone from it); nothing expires old conversations on its own yet.
- **"What is on screen" is a sentence added to the user's request.** It is how the model learns the context, and a small model may take it more or less literally than intended.
- **Expressiveness is capped by the plan's four block types**, plus the screens the agent builds itself (listings, files, images, the editor, proposed changes, run output). New UI means a new block type and a builder, by design.
- **The model only changes small files.** A proposal for a file longer than the 4,000 characters it is shown is refused. The editor handles files up to 64 KB, which the user can change by hand.
- **The A2UI packages are pre-1.0 and moving.** `@a2ui/web_core` 0.12 already exports a v1.0 protocol. Versions are pinned by the lockfile; upgrades go through the contract tests.
- **`@a2ui/react` 0.12.0 ships its components without their CSS class names**, so the Canvas styles the rendered elements itself. Revisit when upstream fixes the build.
- **A 7B model underuses the UI.** The router (M0.7) should be able to send turns to a larger local model, and the default local model is an open question.
- **What the model says is still only as good as the model.** Files on screen come from the tool, but a 7B model will sometimes describe a refusal as a missing file, or offer to rename a file it cannot rename. Capabilities have to be enforced by what tools exist, never by what the prompt says.
- **Every lookup is shown, including exploratory ones.** A model that searches twice and then lists a folder puts three listings on screen. Bounded at three, and truthful, but sometimes cluttered.
- **Tool choice is hand-written per tool.** Each lookup is a typed step in the agent. Third-party MCP servers (Phase 2) will need steps generated from their tool schemas.
- **The catalog id is duplicated** in `canvas/src/a2ui/catalog.tsx` and `agent/src/praxis_agent/ui/a2ui.py`. The contract tests fail if they drift.

## References

- [ADR-001 — Strategic positioning](./ADR-001-strategic-positioning.md)
- [Roadmap](../ROADMAP.md)
- `@a2ui/react` — https://www.npmjs.com/package/@a2ui/react
- A2UI v0.9 — https://developers.googleblog.com/a2ui-v0-9-generative-ui/
