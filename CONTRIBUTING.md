# Contributing to Praxis

Thanks for taking the time. Praxis is open from day one, but it is moving deliberately. Read this in full before opening a PR.

## Phase 0 contribution guidance

We are in **Phase 0 — Spike**. The current goal is to prove the adaptive Canvas concept end-to-end on existing Linux. That means:

- Big architectural changes are welcome **as issues or discussions**, not PRs
- Bug fixes and small improvements to scaffolding are welcome as PRs
- New features should match a milestone in the [Roadmap](docs/ROADMAP.md) — if there's no milestone, open an issue first
- Docs improvements always welcome

Until we ship the M0.13 demo, the codebase will change shape often. Don't take it personally if something gets refactored away.

## How to propose something

1. Check open [issues](../../issues) and [discussions](../../discussions) — your idea may already be there
2. Open a discussion under "Ideas" with the problem you're solving and a sketch of the approach
3. Wait for a maintainer's reaction (usually within a week) before writing significant code
4. Reference the discussion in your PR

## Code standards

| Stack | Linter | Formatter |
|---|---|---|
| Rust (`canvas/src-tauri/`) | `clippy` | `rustfmt` |
| TypeScript (`canvas/src/`) | `eslint` | `prettier` |
| Python (`agent/`) | `ruff` | `ruff format` |

Run `make lint` before pushing (Makefile lands with M0.5).

## Commits

Conventional Commits format: `feat(canvas): add file mode renderer`. The roadmap-tracking commits use: `chore(roadmap): mark M0.X complete — <one-line summary>`.

## Code of Conduct

By participating, you agree to abide by the [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) (Contributor Covenant 2.1).

## Security

Found a vulnerability? Don't open a public issue. See [`SECURITY.md`](SECURITY.md) for the responsible disclosure process.

## Ownership

By contributing, you agree your contribution is licensed under Apache 2.0 (the project's license) and that you have the right to license it.
