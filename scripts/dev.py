"""Start the whole Praxis stack for development, and stop it all with Ctrl+C.

    python scripts/dev.py          # proxy, agent and the Canvas desktop window
    python scripts/dev.py --web    # the Canvas in a browser instead (no Rust needed)

Needs uv, Node with corepack, and Ollama running with the model in
agent/litellm_config.yaml pulled. Standard library only, so it runs before anything
is installed.
"""

import argparse
import json
import os
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--web", action="store_true", help="open the Canvas in a browser")
    parser.add_argument(
        "--model", help="model alias to serve every turn (default: the agent's own default)"
    )
    args = parser.parse_args()

    missing = [tool for tool in ("uv", "corepack") if not shutil.which(tool)]
    if missing:
        print(f"Not found on PATH: {', '.join(missing)}. See the README quickstart.")
        return 1
    check_ollama()

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYDANTIC_AI_NO_BANNER="1")
    if args.model:
        env["PRAXIS_MODEL"] = args.model
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


def check_ollama() -> None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as response:
            models = {m["name"] for m in json.load(response)["models"]}
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        print("Warning: Ollama is not answering on port 11434. Local models will fail.")
        return
    if "qwen2.5:7b" not in models:
        print("Warning: qwen2.5:7b is not pulled. Run: ollama pull qwen2.5:7b")


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
