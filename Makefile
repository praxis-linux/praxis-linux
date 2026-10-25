# The checks CI runs. Needs uv, pnpm and a Rust toolchain.

.PHONY: lint test

lint:
	cd mcp-servers/praxis-fs && uv run ruff check . && uv run ruff format --check .
	cd agent && uv run ruff check . && uv run ruff format --check .
	cd canvas && pnpm typecheck
	# The Tauri build embeds canvas/dist, so it has to exist before cargo runs.
	cd canvas && pnpm build
	cd canvas/src-tauri && cargo fmt --check && cargo clippy --all-targets -- -D warnings

test:
	cd mcp-servers/praxis-fs && uv run pytest
	cd agent && uv run pytest
	cd canvas && pnpm test
	# Needs canvas/dist, which `make lint` builds.
	cd canvas/src-tauri && cargo test
