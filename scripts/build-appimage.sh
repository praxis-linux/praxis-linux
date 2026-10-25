#!/usr/bin/env bash
# Build Praxis as an AppImage: the Canvas, with the agent, its MCP servers and the
# model proxy inside, on a Python of their own. The Canvas starts them itself (see
# canvas/src-tauri/src/services.rs). Ollama is not inside: local models need it
# installed; cloud models need an API key in the environment.
#
#   scripts/build-appimage.sh                  # the AppImage
#   scripts/build-appimage.sh --services-only  # only canvas/src-tauri/target/praxis-services
#
# Linux only. Needs uv, Node with corepack, Rust and the Tauri prerequisites. The
# AppImage runs on Linux systems with a glibc at least as new as the one it was
# built on, so build on the oldest system it should run on.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SERVICES="$ROOT/canvas/src-tauri/target/praxis-services"
PYTHON_VERSION=3.13
LITELLM="litellm[proxy]==1.103.1" # Keep in step with scripts/dev.py.
# Packages the proxy pulls in for features Praxis does not use (spend analytics,
# profiling, other web servers), and the largest part of it. numpy and soundfile
# (with the libsndfile it carries) stay: the proxy imports both on start. The check
# below fails the build if pruning broke it, but only where the system lacks what
# was pruned, so test the AppImage on a bare system too.
PRUNE=(polars polars_runtime_32 _polars_runtime_32 pyroscope granian)

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
rm -rf "$SERVICES"
mkdir -p "$SERVICES"

echo "== A Python of its own ($PYTHON_VERSION)"
export UV_PYTHON_INSTALL_DIR="$WORK/pythons"
uv python install --quiet "$PYTHON_VERSION"
found=$(uv python find --managed-python "$PYTHON_VERSION")
# uv may hand back a path through a link; copy the installation itself.
cp -a "$(realpath "$(dirname "$(dirname "$found")")")" "$SERVICES/python"
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
for package in "${PRUNE[@]}"; do
  rm -rf "$SERVICES/litellm/$package" "$SERVICES/litellm/$package"-*.dist-info
done
cp "$ROOT/agent/litellm_config.yaml" "$SERVICES/"

echo "== Checking the services start"
env -u PYTHONPATH "$PYTHON" -c "import praxis_agent.server, praxis_fs.server, praxis_run.server"
port=$(env -u PYTHONPATH "$PYTHON" -c "import socket; s=socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1])")
PYTHONPATH="$SERVICES/litellm" "$PYTHON" -m litellm.proxy.proxy_cli \
  --config "$SERVICES/litellm_config.yaml" --host 127.0.0.1 --port "$port" >"$WORK/proxy.log" 2>&1 &
proxy=$!
for _ in $(seq 120); do
  curl -sf "http://127.0.0.1:$port/health/liveliness" >/dev/null && break
  kill -0 "$proxy" 2>/dev/null || break
  sleep 1
done
if ! curl -sf "http://127.0.0.1:$port/health/liveliness" >/dev/null; then
  tail -30 "$WORK/proxy.log"
  echo "The bundled model proxy did not start." >&2
  exit 1
fi
kill "$proxy"
du -sh "$SERVICES"
if [[ "${1:-}" == "--services-only" ]]; then
  exit 0
fi

echo "== The AppImage"
cd "$ROOT/canvas"
corepack pnpm install --frozen-lockfile
corepack pnpm tauri build --bundles appimage --config src-tauri/tauri.appimage.conf.json
appimage=$(ls "$PWD"/src-tauri/target/release/bundle/appimage/*.AppImage)

echo "== Leaving Wayland's libraries to the system"
# Like the graphics drivers they go with, they must match the system: a bundled copy
# older than the system's Mesa stops WebKit with "Could not create default EGL display"
# (seen on Arch). linuxdeploy bundles them anyway, so the AppImage is repacked without
# them, with the appimagetool Tauri downloaded for its own build.
tool="$HOME/.cache/tauri/linuxdeploy-plugin-appimage.AppImage"
(cd "$WORK" && "$tool" --appimage-extract >/dev/null && mv squashfs-root tool)
(cd "$WORK" && "$appimage" --appimage-extract >/dev/null)
rm -f "$WORK"/squashfs-root/usr/lib/libwayland-{client,cursor,egl,server}.so*
PATH="$WORK/tool/appimagetool-prefix/usr/bin:$PATH" ARCH=x86_64 \
  appimagetool --no-appstream "$WORK/squashfs-root" "$appimage" >"$WORK/appimagetool.log" 2>&1 ||
  { cat "$WORK/appimagetool.log"; exit 1; }
ls -lh "$appimage"
