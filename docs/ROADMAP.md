# Praxis Roadmap

This document is the single source of truth for project progress. Every status update should refer back to this file. Progress percentages are computed by counting checked milestones against total milestones in each phase.

## Phase summary

| Phase | Goal | Status |
|---|---|---|
| **0 — Spike** | Canvas as a Linux app, runnable on Ubuntu/Arch | 🟠 In progress |
| **1 — Live Disc** | Bootable ISO, Calamares installer, custom theme | ⚪ Not started |
| **2 — Ecosystem** | Plugin SDK, NixOS variant, Praxis Hub | ⚪ Not started |
| **3 — Adoption** | LTS edition, OEM partnerships, certifications | ⚪ Not started |

---

## Phase 0 · Spike (current)

**Goal:** Prove the adaptive Canvas concept on existing Linux. No ISO yet. Three modes working: Conversation, File, Code.

**Exit criteria:** A 90-second video demo showing intent → mode-switch → useful action, runnable as an AppImage or Flatpak on Arch and Ubuntu.

### Milestones

- [x] **M0.1** — Strategic foundation: manifesto, name locked (Praxis), license chosen (Apache 2.0)
- [x] **M0.2** — Public repository created and scaffolded, pushed to `github.com/praxis-linux/praxis-linux`
- [ ] **M0.3** — Public presence reserved: GitHub org (`praxis-linux`) ✅, X handle (`@PraxisLinux`), domain (`praxislinux.dev`)
- [x] **M0.4** — Dev toolchain installed locally (Rust, Node, pnpm, Python, Ollama)
- [x] **M0.5** — Tauri canvas app scaffolded and renders a minimal A2UI payload
- [x] **M0.6** — Backend agent service stood up (FastAPI + PydanticAI); Canvas renders its A2UI v0.9 output ([ADR-003](architecture/ADR-003-ui-wire-format.md))
- [ ] **M0.7** — LiteLLM proxy wired to Ollama (local) ✅ and one cloud provider (Anthropic or OpenAI)
- [x] **M0.8** — First MCP server (filesystem) integrated end-to-end — `praxis-fs`, read-only; write and move wait for M0.12
- [x] **M0.9** — Conversation mode functional (chat, streaming, memory) — conversations stored locally; long-term vector memory not started
- [ ] **M0.10** — File mode functional (browse ✅, preview: text only so far, search via intent ✅)
- [ ] **M0.11** — Code mode functional (open, edit, run a file with CodeMirror)
- [x] **M0.12** — Privacy guardrails: audit log, cloud-call toast, kill switch hotkey (Esc, in the Canvas window; a system-wide hotkey comes with the Phase 1 session), deleting a conversation
- [ ] **M0.13** — 90-second demo video published; pre-launch post drafted

**Phase 0 progress: 8 / 13 milestones complete (~62%).**

---

## Phase 1 · Live Disc (planned)

**Goal:** Wrap Phase 0 into a bootable ISO. Power user can `dd` it to a USB and have a working AI desktop in 15 minutes.

### Provisional milestones (will be refined when Phase 0 is ~80% done)

- [ ] M1.1 — Fork archiso build pipeline
- [ ] M1.2 — Pre-installed: NVIDIA 550+ / ROCm 6 / Intel NPU drivers
- [ ] M1.3 — Pre-installed: Ollama, Canvas, MCP server suite
- [ ] M1.4 — Default model bundle baked in (qwen2.5:7b at minimum)
- [ ] M1.5 — Custom Calamares installer with AI-aware setup flow
- [ ] M1.6 — Btrfs + snapper default; agent actions auto-snapshot
- [ ] M1.7 — Branded boot splash, theme, default wallpaper, icon set
- [ ] M1.8 — Hardware Compatibility List (HCL) and `praxis-doctor` CLI
- [ ] M1.9 — First public ISO release on GitHub Releases

---

## Phase 2 · Ecosystem (planned)

- [ ] M2.1 — Plugin SDK published; third-party MCP servers and component packs
- [ ] M2.2 — NixOS flake variant for reproducible builds
- [ ] M2.3 — Ansible/Terraform agent integrations (configure remote machines via intent)
- [ ] M2.4 — Multi-agent workflow composer
- [ ] M2.5 — Praxis Hub (federated model + skill marketplace)
- [ ] M2.6 — Community Discord/Matrix at scale; monthly contributor calls

---

## Phase 3 · Adoption (planned)

- [ ] M3.1 — Debian-based LTS edition for regulated industries
- [ ] M3.2 — Hardware partnerships (Framework / System76 / Tuxedo)
- [ ] M3.3 — Compliance: SOC 2-aligned posture, NIST AI RMF mapping, ISO 27001 readiness
- [ ] M3.4 — Foundation governance model (Linux Foundation sub-project or independent)
- [ ] M3.5 — Sustainable funding (sponsors, paid LTS support, no VC)

---

## How we track

After every working session, update the checkboxes above. The phase percentage is `checked / total × 100`, rounded. Commit message format: `chore(roadmap): mark M0.X complete — <one-line summary>`.
