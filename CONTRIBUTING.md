# Contributing to Praxis

Thanks for taking the time. Praxis is open from day one, but it is moving deliberately. Read this in full before opening a PR.

## Phase 0 contribution guidance

We are in **Phase 0 — Spike**. The current goal is to prove the adaptive Canvas concept end-to-end on existing Linux. That means:

- Big architectural changes are welcome **as issues or discussions**, not PRs
- Bug fixes and small improvements to scaffolding are welcome as PRs
- New features should match a milestone in the [Roadmap](docs/ROADMAP.md) — if there's no milestone, open an issue first
- Docs improvements always welcome

Until we ship the M0.13 demo, the codebase will change shape often. Don't take it personally if something gets refactored away.

## Getting set up

The [README](README.md#develop-run-it-from-the-source) shows how to run Praxis from the source: `python scripts/dev.py` starts the model proxy, the agent and the Canvas. Each part has its own README with its tests. ADR-003 is the contract between the agent and the Canvas, and the place to start before changing either.

## How to propose something

1. Check open [issues](../../issues) and [discussions](../../discussions) — your idea may already be there
2. Open a discussion under "Ideas" with the problem you're solving and a sketch of the approach
3. Wait for a maintainer's reaction (usually within a week) before writing significant code
4. Reference the discussion in your PR

## Code standards

| Stack | Linter | Formatter |
|---|---|---|
| Rust (`canvas/src-tauri/`) | `clippy` | `rustfmt` |
| TypeScript (`canvas/src/`) | `tsc` (eslint not set up yet) | not set up yet |
| Python (`agent/`, `mcp-servers/*`) | `ruff` | `ruff format` |

Run `make lint` and `make test` before pushing (they need uv, pnpm and Rust). CI runs the same checks on every pull request, and builds the AppImage when the Tauri shell or the build script changes.

Changes to what the agent may do to files, or to the sandbox, get the most scrutiny: say in the PR what a model or a file it reads could make the change do.

## Commits

Conventional Commits format: `feat(canvas): add file mode renderer`. The roadmap-tracking commits use: `chore(roadmap): mark M0.X complete — <one-line summary>`.

## Code of Conduct

By participating, you agree to uphold the [Code of Conduct](CODE_OF_CONDUCT.md), adapted from the Contributor Covenant 2.1.

## Security

Found a vulnerability? Don't open a public issue. Report it privately as described in [`SECURITY.md`](SECURITY.md).

## Ownership

By contributing, you agree your contribution is licensed under Apache 2.0 (the project's license) and that you have the right to license it.
