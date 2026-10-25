# The 90-second demo

The Phase 0 exit demo: intent, the Canvas changing mode, and a useful action. It runs on the packaged app, against a folder made for it, so it can be recorded again the same way.

**The recording: [https://youtu.be/MgkO89fFu8c](https://youtu.be/MgkO89fFu8c)** (unlisted on YouTube).

## What it shows

1. You type what you want; the Canvas becomes what that needs. Conversation, File mode and Code mode, one surface.
2. What the agent shows about your files comes from your files, not from the model's retelling.
3. The agent proposes, you decide: a change is a diff until you apply it, and every change can be undone.
4. Code runs in a sandbox with no network access, which cannot change your files.
5. Local first: the model runs on your machine. A cloud model is announced before every request.

## Before recording

1. **Build or download the AppImage** (`scripts/build-appimage.sh`), and install [Ollama](https://ollama.com) with `ollama pull qwen2.5:7b`. For Run, install bubblewrap: `sudo apt install bubblewrap`, `sudo pacman -S bubblewrap`.
2. **Make the demo folder**: `examples/90s-demo/setup.sh`. It creates `~/praxis-demo` with two documents and a small project, `projects/tally`, whose script has a bug.
3. **Start Praxis on it**: `PRAXIS_FS_ROOTS=~/praxis-demo ./Praxis*.AppImage`. The agent then sees only the demo folder.
4. **Warm the model**: ask anything once, then press **New** for a clean conversation. The first request after Ollama loads the model takes up to a minute; later ones take one to five seconds.
5. **Window**: 1280×800, nothing else on screen. Record at 30 fps or more (GNOME's built-in recorder, or OBS).

Do a full run-through first: a 7B model words things differently each time, and sometimes needs the request said twice. If it does, cut the retry.

## Shot list

| Time | Do | On screen | Caption (optional) |
|---|---|---|---|
| 0–6 s | The Canvas is open on its welcome screen. | Welcome, the intent bar | *The desktop is the agent.* |
| 6–20 s | Type **what did I work on this week?** | "Looking for files changed in the last 7 days…", then the files, newest first; the mode badge turns to FILE | *Intent, not menus.* |
| 20–32 s | Click **q3-report.md**, then type **summarise this** | The report, then a short summary in the agent's words | *Your files, shown as they are.* |
| 32–44 s | Type **show me projects/tally/tally.py**, then click **Run** | The script; the run fails with `ValueError: invalid literal for int()` | *Code runs sandboxed. No network.* |
| 44–62 s | Type **fix the error** | "Writing a change to …tally.py…", then the diff: `int(` becomes `float(`; Apply and Discard; the mode badge says CODE | *The agent proposes. You decide.* |
| 62–72 s | Click **Apply**, then **Run** | "Saved …", then `Spent in October: 531.70` | |
| 72–80 s | Click **Undo** on the saved change, then show the conversation trail on the right | "Undone: … is back as it was." | *Every change can be undone.* |
| 80–90 s | End card | Praxis Linux · Phase 0 · Apache 2.0 · github.com/praxis-linux/praxis-linux | *Local first. Open all the way down.* |

If time allows, swap the Undo shot for the kill switch: type a long request and press **Esc** while the reply streams.

## What not to claim in the voice-over

Praxis is a Phase 0 spike: the Canvas runs as an app on an existing Linux, not yet as a distribution. Moving and deleting files, Doc mode and the settings panel are not built. Running code is limited to Python, shell and JavaScript files, and needs bubblewrap.
