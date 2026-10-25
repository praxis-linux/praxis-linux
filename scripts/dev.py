"""Start the whole Praxis stack for development, and stop it all with Ctrl+C.

    python scripts/dev.py          # proxy, agent and the Canvas desktop window
    python scripts/dev.py --web    # the Canvas in a browser instead (no Rust needed)
    python scripts/dev.py --wsl    # on Windows: run it all in WSL, window on the desktop

Needs uv, Node with corepack, and Ollama running with the model in
agent/litellm_config.yaml pulled. Standard library only, so it runs before anything
is installed.
"""

import argparse
import json
import os
import platform
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LITELLM = "litellm[proxy]==1.103.1"  # Keep in step with agent/README.md.
PROXY_PORT = 4000
AGENT_PORT = 7729
CANVAS_URL = "http://localhost:1420"
OLLAMA_PORT = 11434
WSL_DISTRO = "Ubuntu-24.04"
# Where --wsl copies the working tree inside WSL. Under a dot-folder, so the agent,
# which reads the WSL home folder, does not list it.
WSL_COPY = "~/.cache/praxis-dev/praxis-linux"
# What --wsl does not copy: each side builds its own.
WSL_SKIP = [".git", "node_modules", ".venv", "target", "dist", "__pycache__", ".*_cache"]
# Keys the proxy reads, handed from Windows to WSL when they are set.
WSL_FORWARD = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LITELLM_MASTER_KEY"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--web", action="store_true", help="open the Canvas in a browser")
    parser.add_argument(
        "--model", help="model alias to serve every turn (default: the agent's own default)"
    )
    parser.add_argument(
        "--wsl",
        nargs="?",
        const=WSL_DISTRO,
        metavar="DISTRO",
        help=f"on Windows: run everything in a WSL distro (default {WSL_DISTRO})",
    )
    args = parser.parse_args()
    if args.wsl:
        return run_in_wsl(args)

    missing = [tool for tool in ("uv", "corepack") if not shutil.which(tool)]
    if missing:
        print(f"Not found on PATH: {', '.join(missing)}. See the README quickstart.")
        return 1
    if not args.web and not shutil.which("cargo"):
        print(
            "The desktop window needs Rust (cargo), which is not on PATH. Run with --web to\n"
            "use the Canvas in a browser instead, or install the Tauri prerequisites:\n"
            "https://tauri.app/start/prerequisites/"
        )
        return 1
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYDANTIC_AI_NO_BANNER="1")
    # A new secret each start, known to the agent and the Canvas only: other programs
    # on this machine can reach the agent's port, but not act through it.
    env["PRAXIS_AGENT_TOKEN"] = env["VITE_PRAXIS_AGENT_TOKEN"] = secrets.token_urlsafe(32)
    ollama = find_ollama()
    if ollama:
        env["OLLAMA_API_BASE"] = ollama
    if args.model:
        env["PRAXIS_MODEL"] = args.model
    if in_wsl():
        # WebKitGTK draws a blank window under WSLg without this.
        env.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
    processes: list[tuple[str, subprocess.Popen]] = []

    def start(name: str, command: list[str], cwd: Path) -> None:
        print(f"Starting {name}: {' '.join(command)}")
        processes.append((name, subprocess.Popen(command, cwd=cwd, env=env, **new_group())))

    try:
        start(
            "model proxy",
            ["uv", "tool", "run", "--from", LITELLM, "litellm", "--config",
             "litellm_config.yaml", "--host", "127.0.0.1", "--port", str(PROXY_PORT)],
            ROOT / "agent",
        )
        wait_for(f"http://127.0.0.1:{PROXY_PORT}/health/liveliness", "model proxy", processes)

        start(
            "agent",
            ["uv", "run", "uvicorn", "praxis_agent.server:create_app", "--factory",
             "--host", "127.0.0.1", "--port", str(AGENT_PORT)],
            ROOT / "agent",
        )
        wait_for(f"http://127.0.0.1:{AGENT_PORT}/health", "agent", processes)

        corepack = shutil.which("corepack") or "corepack"
        if not (ROOT / "canvas" / "node_modules").exists():
            subprocess.run([corepack, "pnpm", "install"], cwd=ROOT / "canvas", check=True)
        start(
            "Canvas",
            [corepack, "pnpm", "dev" if args.web else "tauri", *([] if args.web else ["dev"])],
            ROOT / "canvas",
        )
        if args.web:
            wait_for(CANVAS_URL, "Canvas", processes)
            print(f"\nPraxis is running. Open {CANVAS_URL} in a browser.")
        else:
            print("\nPraxis is starting. The Canvas window opens when the desktop build is done.")
        print("Press Ctrl+C to stop everything.\n")

        while True:
            for name, process in processes:
                if process.poll() is not None:
                    print(f"\n{name} stopped (exit code {process.returncode}); stopping the rest.")
                    return 1
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
        return 0
    except RuntimeError as error:
        print(f"\n{error}")
        return 1
    finally:
        for _, process in reversed(processes):
            stop(process)


def find_ollama() -> str | None:
    """Where Ollama answers. Under WSL that may be Windows, which is not localhost there."""
    if os.environ.get("OLLAMA_API_BASE"):
        candidates = [os.environ["OLLAMA_API_BASE"].rstrip("/")]
    else:
        candidates = [f"http://127.0.0.1:{OLLAMA_PORT}"]
        host = windows_host() if in_wsl() else None
        if host:
            candidates.append(f"http://{host}:{OLLAMA_PORT}")
    for base in candidates:
        try:
            with urllib.request.urlopen(f"{base}/api/tags", timeout=3) as response:
                models = {m["name"] for m in json.load(response)["models"]}
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            continue
        if "qwen2.5:7b" not in models:
            print("Warning: qwen2.5:7b is not pulled. Run: ollama pull qwen2.5:7b")
        return base
    print(f"Warning: Ollama is not answering at {' or '.join(candidates)}. Local models will fail.")
    return None


def in_wsl() -> bool:
    return sys.platform == "linux" and "microsoft" in platform.release().lower()


def windows_host() -> str | None:
    """The Windows side of WSL's network: the default gateway."""
    try:
        for line in Path("/proc/net/route").read_text().splitlines()[1:]:
            fields = line.split()
            if fields[1] == "00000000":
                return ".".join(str(b) for b in bytes.fromhex(fields[2])[::-1])
    except (OSError, IndexError, ValueError):
        pass
    return None


def run_in_wsl(args: argparse.Namespace) -> int:
    """Copy this working tree into WSL and run this script there.

    The Canvas window then comes up on the Windows desktop through WSLg. Builds and
    environments stay apart: WSL makes its own, and nothing here is touched.
    """
    if os.name != "nt":
        print("--wsl is for Windows. Here, run the script without it.")
        return 1
    if not shutil.which("wsl"):
        print("WSL is not installed. See https://learn.microsoft.com/windows/wsl/install")
        return 1
    distro = args.wsl
    found = subprocess.run(["wsl", "-d", distro, "-e", "wslpath", "-a", ROOT.as_posix()],
                           capture_output=True, text=True)
    if found.returncode != 0:
        print(f"Could not use the WSL distro {distro!r}. Installed: wsl --list --verbose")
        return 1
    source = found.stdout.strip()
    skip = " ".join(f"--exclude={shlex.quote(name)}" for name in WSL_SKIP)
    passed = [*(["--web"] if args.web else []), *(["--model", args.model] if args.model else [])]
    script = (
        f"set -e; mkdir -p {WSL_COPY}; "
        f"rsync -a --delete {skip} {shlex.quote(source)}/ {WSL_COPY}/; "
        f"cd {WSL_COPY}; exec python3 scripts/dev.py {shlex.join(passed)}"
    )
    env = dict(os.environ)
    shared = [name for name in WSL_FORWARD if os.environ.get(name)]
    env["WSLENV"] = ":".join(filter(None, [os.environ.get("WSLENV"), *shared]))
    print(f"Copying this working tree into WSL ({distro}) and starting Praxis there.")
    print("The first start builds the desktop app, which takes a few minutes.\n", flush=True)
    process = subprocess.Popen(["wsl", "-d", distro, "-e", "bash", "-lc", script], env=env)
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            # Ctrl+C reaches the script inside WSL too; let it stop what it started.
            continue


def wait_for(url: str, name: str, processes: list, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(process.poll() is not None for _, process in processes):
            raise RuntimeError(f"Something stopped while waiting for the {name}.")
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    raise RuntimeError(f"The {name} did not come up at {url} within {int(timeout)}s.")


def new_group() -> dict:
    # Each child gets its own process group, so stopping it stops what it started too.
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        # taskkill /T ends the whole tree: pnpm, vite and uv all start children.
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


if __name__ == "__main__":
    sys.exit(main())
