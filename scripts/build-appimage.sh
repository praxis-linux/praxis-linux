#!/usr/bin/env bash
# Build Praxis as an AppImage: the Canvas, with the agent, its MCP servers and the
# model proxy inside, on a Python of their own. The Canvas starts them itself (see
# canvas/src-tauri/src/services.rs). Ollama is not inside: local models need it
# installed; cloud models need an API key in the environment.
#
#   scripts/build-appimage.sh
#
# Linux only. Needs uv, Node with corepack, Rust and the Tauri prerequisites. The
# AppImage runs on Linux systems with a glibc at least as new as the one it was
# built on, so build on the oldest system it should run on.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SERVICES="$ROOT/canvas/src-tauri/target/praxis-services"
PYTHON_VERSION=3.13
LITELLM="litellm[proxy]==1.103.1" # Keep in step with scripts/dev.py.

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
rm -rf "$SERVICES"
mkdir -p "$SERVICES"

echo "== A Python of its own ($PYTHON_VERSION)"
export UV_PYTHON_INSTALL_DIR="$WORK/pythons"
uv python install --quiet "$PYTHON_VERSION"
found=$(uv python find --managed-python "$PYTHON_VERSION")
cp -a "$(dirname "$(dirname "$found")")" "$SERVICES/python"
unset UV_PYTHON_INSTALL_DIR
PYTHON="$SERVICES/python/bin/python3"
# Not needed to run the services, and large.
rm -rf "$SERVICES"/python/lib/python3.*/{test,idlelib,tkinter,turtledemo,ensurepip}
rm -f "$SERVICES"/python/lib/python3.*/EXTERNALLY-MANAGED

echo "== The agent and its MCP servers, at the versions in agent/uv.lock"
(cd "$ROOT/agent" && uv export --frozen --no-dev --no-hashes --no-emit-project --no-emit-local) \
  >"$WORK/requirements.txt"
uv pip install --quiet --python "$PYTHON" -r "$WORK/requirements.txt"
uv pip install --quiet --python "$PYTHON" --no-deps \
  "$ROOT/mcp-servers/praxis-fs" "$ROOT/mcp-servers/praxis-run" "$ROOT/agent"

echo "== The model proxy, kept apart: its pins differ from the agent's"
uv pip install --quiet --python "$PYTHON" --target "$SERVICES/litellm" "$LITELLM"
cp "$ROOT/agent/litellm_config.yaml" "$SERVICES/"

echo "== Checking the services load"
env -u PYTHONPATH "$PYTHON" -c "import praxis_agent.server, praxis_fs.server, praxis_run.server"
PYTHONPATH="$SERVICES/litellm" "$PYTHON" -c "import litellm.proxy.proxy_cli"
du -sh "$SERVICES"

echo "== The AppImage"
cd "$ROOT/canvas"
corepack pnpm install --frozen-lockfile
corepack pnpm tauri build --bundles appimage --config src-tauri/tauri.appimage.conf.json
ls -lh src-tauri/target/release/bundle/appimage/*.AppImage
