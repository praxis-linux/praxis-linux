# Pre-launch post (draft)

*For a project blog post, Show HN, r/linux and the like. Replace the video link once it is recorded; check the numbers against the roadmap before posting.*

---

## Praxis: a Linux desktop where the desktop is the agent

Most "AI on the desktop" today is a sidebar: a chat window next to the apps you already use, which can talk about your work but not become it. Praxis tries the other shape. There is one surface, the Canvas. You say what you want, and it becomes what that needs: a file browser when you ask about files, an editor when you ask about code, a conversation when you just want to talk.

[90-second demo video]

This is Phase 0, a spike: the Canvas runs as an app on an existing Linux (an AppImage, tested on Ubuntu 24.04 and Arch Linux). If it holds up, Phase 1 turns it into a bootable distribution.

### What works today

- **Conversation, File and Code modes on one surface.** Ask "what did I work on this week?" and the Canvas lists those files; click one to read it, ask for a summary, open it in the editor, run it.
- **The agent proposes; you decide.** Ask for a change to a file and you get a diff with Apply and Discard. Nothing is written until you apply it, every write can be undone, and a file changed since it was read is never overwritten.
- **Code runs in a sandbox.** Run uses bubblewrap: no network, system folders read-only, and the code can read its own folder but never change your files.
- **Local first.** A 7B model on Ollama drives everything above. Cloud models (Claude, via a LiteLLM proxy you configure) are optional, and every cloud request is announced before it is sent.
- **Legible and stoppable.** Every model call, file read, write and run is written to an audit log before it happens. Esc stops whatever the agent is doing.

### How it is built

- **The model never writes UI.** It fills in a small, schema-constrained plan; code compiles that into [A2UI](https://github.com/google/A2UI) v0.9, which the Canvas renders with the official React renderer from a vetted catalog. Components that could load remote content are left out, so even a confused or manipulated model cannot make the Canvas fetch anything or navigate away. A 7B model produced a valid screen on every one of the 36 file-lookup turns we tested.
- **What you see about your files comes from your files.** Listings and file contents go on screen straight from the tool, not the model's retelling. The model adds a sentence about them.
- **Tools are MCP servers with narrow jobs.** `praxis-fs` reads and writes inside the folders you allow, and refuses hidden files like `.ssh` and `.env`. `praxis-run` runs one file in the sandbox. The model can ask for lookups; only your clicks write files or run code.
- **Tauri + React for the Canvas, Python (FastAPI + PydanticAI) for the agent.** Apache 2.0.

The design decisions, with the evidence behind them (including where the 7B model falls short), are in [ADR-003](../architecture/ADR-003-ui-wire-format.md).

### What does not work yet

- It is not a distribution yet: no ISO, no installer, no session of its own.
- No moving or deleting files, no Doc mode, no settings panel.
- The model can change files of up to 4,000 characters; you can edit larger ones by hand.
- The sandbox does not limit memory or the number of processes yet.
- A 7B model uses the UI modestly: it seldom reaches for forms or follow-up cards unless asked. Larger local models do better.
- Files can carry instructions aimed at the model (prompt injection). The design bounds what that can do: no write or run without your click, a diff before every change, no remote loads. It does not prevent it.

### Why we are posting now

We want people who will push on it: try the AppImage, read the ADRs, tell us where the security model is wrong, and help decide what the Canvas should become next. Issues and pull requests are welcome; see [CONTRIBUTING.md](../../CONTRIBUTING.md). Security reports go through GitHub's private reporting; see [SECURITY.md](../../SECURITY.md).

Repository: https://github.com/praxis-linux/praxis-linux
