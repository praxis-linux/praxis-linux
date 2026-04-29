# ADR-001: Strategic positioning, base distro, and core stack

**Status:** Accepted
**Date:** 2026-04-25
**Decision-makers:** Founding contributor

## Context

Praxis exists in a crowded space: AI-native chat sidebars (Microsoft Copilot+, Apple Intelligence), AI-flavoured Linux distros (MAGI OS on Debian, Ubuntu AI), and generative-UI frameworks (A2UI, Thesys C1, Vercel AI SDK). To avoid being "yet another," we must take sharp positions early.

This ADR locks in the decisions that downstream work depends on. Reverse them only with another ADR.

> Naming and namespace decisions are captured separately in [ADR-002](./ADR-002-name-and-namespace.md).

## Decision

### 1. Positioning thesis

**"The Linux distribution where the desktop is the agent."** Not Linux with AI features. Not a chatbot for Linux. The desktop itself is conversational; UI is generated from intent rather than selected from a menu.

### 2. Target users (priority order)

1. AI engineers and ML researchers
2. Developers building agentic apps
3. Privacy-conscious knowledge workers (legal, healthcare, gov)
4. Linux enthusiasts currently on Arch / Fedora / Pop!_OS

**Explicit non-goal:** non-technical daily-driver users in v1. Kali doesn't try to be Ubuntu; Praxis shouldn't either.

### 3. Base distribution: Arch (upstream)

Three real options were considered.

| Base | Verdict | Reasoning |
|---|---|---|
| Arch | ✅ Chosen | Rolling release; latest kernel/CUDA/ROCm matters for AI; AUR; target users are comfortable with Arch |
| Debian/Ubuntu | Phase 3 (LTS edition) | Older kernels lag NPU/GPU support; revisit for regulated-industry edition |
| NixOS | Phase 2 (variant) | Declarative + reproducible aligns beautifully with an OS the AI mutates; smaller ecosystem makes it a v2 differentiator, not v1 base |

### 4. Compositor: Hyprland (forked)

Wayland-based, scriptable, modern. We strip the tiling-WM defaults and ship a single full-screen Canvas as the default session. Power users drop into the underlying tiling WM with a hotkey.

### 5. Generative UI: declarative, not open-ended

We render UI from a vetted catalog of components composed by the agent — *not* arbitrary HTML/CSS/JS. Wire format: **A2UI v0.9** (Google-led, framework-agnostic, has a React renderer, supports streaming).

Rationale: open-ended generation has unsolvable problems for a desktop OS — security (arbitrary code), accessibility (no a11y guarantees), consistency (visually different every render). Static catalogs are too rigid. Declarative is the converged-upon middle ground (CopilotKit, Thesys, Google all chose it).

### 6. Canvas runtime: Tauri 2 + React 19 + TypeScript

Lighter than Electron. Rust core gives us system integration and security. React 19 + shadcn/ui + Tailwind v4 for the catalog because it's where the GenUI ecosystem lives.

### 7. Local model runtime: Ollama (default), llama.cpp (embedded), vLLM (workstation)

Ollama is the de facto standard with MCP support. Ship a model bundle sized to detected hardware at install time:

| Detected hardware | Default bundle |
|---|---|
| < 8 GB RAM | Phi-4-mini, Qwen 2.5 3B |
| 8–16 GB RAM | Llama 3.3 8B, Qwen 2.5 7B Coder |
| 16–32 GB + GPU | Qwen 2.5 14B, Phi-4 14B, DeepSeek-Coder 16B |
| 32 GB+ / multi-GPU | Llama 3.3 70B quantized, Qwen 2.5 32B |

The smallest bundle must work without a GPU. The installer detects VRAM and selects accordingly.

### 8. Cloud abstraction: LiteLLM proxy

Pluggable, BYO-key. Users can point Praxis at Anthropic, OpenAI, Gemini, Mistral, Together, Groq, or self-hosted endpoints without code changes. Never lock to one provider.

### 9. Tool protocol: MCP (Model Context Protocol)

Industry standard now (Anthropic, Ollama, Claude, Cursor, OpenAI all support it). All capabilities — filesystem, shell, browser, code, settings, third-party apps — are exposed as MCP servers.

### 10. Memory: Qdrant + SQLite + DuckDB

Qdrant (Rust, embeddable) for vector memory; SQLite for structured state; DuckDB for analytical/log queries. All on-device by default.

### 11. License: Apache 2.0

Apache (not GPL) to maximise downstream adoption — both individuals and companies. GPL components in the kernel are unaffected.

### 12. Privacy posture: legible, reversible, consented

Every cloud call shows a transient toast ("Sending N tokens to cloud-strong"). Audit log at `~/.praxis/audit.db`. Btrfs snapshots before any agent-initiated filesystem or system change. Single hotkey kill switch.

### 13. Voice I/O: whisper.cpp (STT) + Piper (TTS)

Both are local, open-weight, and fast enough for real-time desktop use. whisper.cpp handles speech-to-text for the Intent layer; Piper handles text-to-speech for agent responses. Neither sends audio to the cloud.

### 14. Sandboxing: bubblewrap + Flatpak

Agent-executed shell commands run in bubblewrap namespaces. Third-party apps are delivered as Flatpaks. This limits the blast radius of a misconfigured or misled agent action. First-party MCP servers run with the minimum capabilities they need (declared in their manifest).

### 15. Update strategy: A/B Btrfs subvolumes

The system partition is divided into A/B subvolumes. An update writes to the inactive subvolume and flips the bootloader pointer. If the new subvolume fails to boot, the bootloader rolls back automatically. Snapper creates pre/post snapshots for every agent-initiated system change, independently of the A/B cycle.

### 16. Observability: OpenTelemetry (optional)

Agent spans are instrumented with OpenTelemetry. By default traces are written to a local SQLite sink. Users who opt in can forward to Grafana Tempo or any OTLP endpoint. This is never on by default — it is a power-user diagnostic feature.

## Consequences

**Positive:**
- Clear, defensible positioning vs. existing players
- Standards-aligned (A2UI, MCP) — we inherit ecosystem R&D
- Hardware-friendly base (Arch) for the AI-developer audience
- Open license + open protocols + BYO-model = real "open" credibility

**Negative / risks:**
- Arch base means narrower hardware certification story — mitigated by HCL + `praxis-doctor` and the planned Phase 3 LTS Debian edition
- Declarative GenUI constrains expressiveness — mitigated by an extensible component catalog and a clear plugin SDK in Phase 2
- A2UI is young (v0.9) — we accept the risk and contribute upstream

## References

- [Manifesto](../../MANIFESTO.md)
- [Roadmap](../ROADMAP.md)
- [ADR-002 — Name and namespace](./ADR-002-name-and-namespace.md)
- A2UI v0.9 spec — https://developers.googleblog.com/a2ui-v0-9-generative-ui/
- MCP — https://modelcontextprotocol.io
- Google paper: *Generative UI: LLMs are Effective UI Generators* (2025)
- Prior art: MAGI OS — https://github.com/ruapotato/MAGI
