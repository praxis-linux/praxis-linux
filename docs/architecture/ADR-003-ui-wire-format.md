# ADR-003: UI wire format, catalog, and the agent–Canvas contract

**Status:** Accepted
**Date:** 2026-09-30 (sections 8 and 9 and the second evidence table added the same day, with M0.8)
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
| `POST /action` | An A2UI v0.9 client-to-server `action` message | The user clicked a card or submitted a form |

Both require an `X-Praxis-Session` header and answer with one JSON event per line:

| Event | Payload | When |
|---|---|---|
| `call` | `model`, `destination` (`local` or `cloud`) | Before the model is called, after the audit row is written |
| `mode` | `mode` | Once the plan is known |
| `a2ui` | `message` — one A2UI server-to-client message | Once per message |
| `error` | `message` | The turn failed |

### 6. One surface per slot, replaced each turn

The agent addresses two surfaces: `workspace` and `context` (reserved for the context rail; nothing emits it yet). Each turn sends `deleteSurface`, `createSurface`, `updateComponents` and, when there is a form, `updateDataModel`. Deleting a surface that does not exist is a no-op, so the agent does not track what the Canvas is showing.

### 7. Three UI actions

| Action | Context | Agent treats it as |
|---|---|---|
| `intent` | `text` | A new request with that text |
| `submit` | `purpose`, `values` (the form's data) | A request carrying the user's answers |
| `open` | `target`, `kind` (`file` or `directory`) | A click on a file or folder: the agent calls the filesystem tool directly and shows the result. No model is called. |

Any other action name is rejected without calling a model.

A context key must not be named `path` or `call`: A2UI reads an object with such a key as a data binding or a function call, and the context reaches the agent empty. That is why `open` carries `target`.

### 8. The model asks for tools through the same constrained output

Each model step is one schema-constrained object: either a lookup (`search_files`, `recent_files`, `list_directory`, `read_text_file`) or the reply. The agent runs the lookup against the MCP server, tells the model the result, and asks for the next step. After three lookups only a reply is accepted.

Provider function calling is not used. With `qwen2.5:7b` through LiteLLM and Ollama it failed both ways it was tried: alongside constrained output the model never called a tool (0 of 8 turns) and announced files it had not looked up; without constrained output it looped (26 requests in one turn) or called tools that do not exist (7 of 8 turns failed).

Lookups have no optional fields. A small model fills every field it is offered: an optional "changed in the last N days" on the search was set on almost every search, often to 0, and turned plain searches into empty ones. Searching by name and finding recent files are separate lookups instead.

### 9. What was looked up is shown by the agent, not retold by the model

A listing or a file's text goes on the screen straight from the tool result, placed after the model's answer. The model is asked for a sentence about it. The same holds for refusals: when the filesystem server refuses a lookup (a hidden file, a path outside the allowed folders), the screen says so in the agent's words.

This is what makes file names and contents on the Canvas trustworthy with a model that is not: what the model *says* can still be wrong, and the evidence below shows it sometimes is.

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

## Consequences

**Positive:**
- The Canvas is what ADR-001 said it would be: standards-aligned, and we inherit the renderer instead of maintaining one.
- The catalog is an enforced allowlist, which is the security argument for declarative UI made real.
- A small local model can drive the Canvas without ever producing an invalid screen.
- The contract is tested from both sides: the agent's builders write a golden file, and the Canvas tests feed that file to the real renderer.

**Negative / risks:**
- **No token streaming.** A reply appears when the whole plan is ready. Streaming partial plans is possible later; it is not needed for the Phase 0 demo.
- **Expressiveness is capped by the plan's four block types.** New UI means a new block type and a builder, by design.
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
