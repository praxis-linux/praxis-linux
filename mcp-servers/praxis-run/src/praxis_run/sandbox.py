"""Running a file the user picked, inside a bubblewrap sandbox.

What the code can reach: the system's programs and libraries, read-only; a few
files from /etc that programs need to start; a private /tmp; and its own folder,
where it may write, unless that folder is one of the allowed folders itself (the
home folder, usually), which it may only read. Hidden files and folders directly in
its folder are masked.

What it cannot reach: the network, the rest of the home folder, other drives
(/mnt, /media), /run (and with it the user's session and D-Bus), and any process
outside the sandbox. A run is stopped after RUN_SECONDS, and what it prints is
cut off after MAX_OUTPUT bytes.

Not limited: memory, and how many processes the code starts. Those need cgroups,
which come with the Phase 1 session.
"""

import asyncio
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from praxis_fs.fs import AccessError, Files
from pydantic import BaseModel

RUN_SECONDS = 10.0
MAX_OUTPUT = 64_000

# What runs a file, by its suffix.
INTERPRETERS = {".py": "python3", ".sh": "bash", ".js": "node"}
LANGUAGES = {".py": "Python", ".sh": "shell", ".js": "JavaScript"}

# Read-only system folders. On merged-/usr systems /bin and /lib are links into /usr.
SYSTEM = ["/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32", "/opt"]
# From /etc, only what programs need to start: no secrets and no host configuration.
ETC = [
    "/etc/ld.so.cache",
    "/etc/ld.so.conf",
    "/etc/ld.so.conf.d",
    "/etc/alternatives",
    "/etc/localtime",
    "/etc/passwd",
    "/etc/group",
    "/etc/nsswitch.conf",
]

# Per process, inherited by everything the code starts.
FILE_SIZE_LIMIT = 16_000_000
OPEN_FILES_LIMIT = 256


class Status(BaseModel):
    available: bool
    # Why not, when it is not.
    reason: str
    # Suffixes of the files that can be run, such as ".py".
    suffixes: list[str]


class RunResult(BaseModel):
    path: str
    # The command as the user would type it, such as "python3 app.py".
    command: str
    # None when the run was stopped.
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    # True when the output was longer than MAX_OUTPUT and was cut off.
    truncated: bool
    seconds: float


def sandbox_command(
    bwrap: str,
    interpreter: str,
    file: str,
    *,
    writable: bool,
    hidden: list[tuple[str, bool]],
) -> list[str]:
    """The bwrap command that runs `file` with `interpreter`.

    `writable`: whether the file's folder may be written to. `hidden`: the hidden
    entries directly in that folder, as (path, is_folder), which are masked.
    """
    folder = os.path.dirname(file)
    command = [bwrap, "--unshare-all", "--die-with-parent", "--new-session"]
    for path in SYSTEM + ETC:
        command += ["--ro-bind-try", path, path]
    command += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
    command += ["--bind" if writable else "--ro-bind", folder, folder]
    for path, is_folder in hidden:
        command += ["--tmpfs", path] if is_folder else ["--ro-bind", "/dev/null", path]
    command += [
        "--chdir",
        folder,
        "--clearenv",
        "--setenv",
        "PATH",
        f"/usr/local/bin:/usr/bin:/bin:{os.path.dirname(interpreter)}",
        "--setenv",
        "HOME",
        "/tmp",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "--",
        interpreter,
        file,
    ]
    return command


class Runner:
    def __init__(self, files: Files, *, bwrap: str | None = None) -> None:
        self.files = files
        self.bwrap = bwrap if bwrap is not None else shutil.which("bwrap")
        self._status: Status | None = None
        self._interpreters: dict[str, str] = {}

    def status(self) -> Status:
        """Whether files can be run here, and which. Checked once, by starting a sandbox."""
        if self._status is None:
            self._status = self._check()
        return self._status

    async def run(self, path: str) -> RunResult:
        status = self.status()
        if not status.available:
            raise AccessError(status.reason)
        file = self.files.resolve(path)
        if not file.is_file():
            raise AccessError(f"There is no file at {path}.")
        interpreter = self._interpreters.get(file.suffix.lower())
        if interpreter is None:
            kinds = ", ".join(LANGUAGES[suffix] for suffix in status.suffixes)
            raise AccessError(f"Praxis can only run {kinds} files here.")

        folder = file.parent
        command = sandbox_command(
            self.bwrap or "bwrap",
            interpreter,
            str(file),
            writable=folder not in self.files.roots,
            hidden=[] if self.files.allow_hidden else _hidden_in(folder),
        )
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            preexec_fn=_limits,
        )
        assert process.stdout is not None and process.stderr is not None
        output = asyncio.gather(_read(process.stdout), _read(process.stderr))
        timed_out = False
        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), RUN_SECONDS)
        except TimeoutError:
            timed_out = True
            _kill(process.pid)
        except BaseException:
            _kill(process.pid)
            raise
        await process.wait()
        (stdout, cut_out), (stderr, cut_err) = await output
        return RunResult(
            path=str(file),
            command=f"{INTERPRETERS[file.suffix.lower()]} {file.name}",
            exit_code=None if timed_out else process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            truncated=cut_out or cut_err,
            seconds=round(time.monotonic() - started, 2),
        )

    def _check(self) -> Status:
        if not sys.platform.startswith("linux"):
            return Status(available=False, reason="Running code needs Linux.", suffixes=[])
        if not self.bwrap:
            return Status(
                available=False,
                reason=(
                    "Running code needs bubblewrap, which is not installed. "
                    "On Ubuntu: sudo apt install bubblewrap"
                ),
                suffixes=[],
            )
        system = [os.path.realpath(path) for path in SYSTEM if os.path.isdir(path)]
        for suffix, name in INTERPRETERS.items():
            found = shutil.which(name)
            # By its real path, and only if the sandbox can see it: one in a virtual
            # environment or the home folder is not mounted.
            real = os.path.realpath(found) if found else ""
            if real and any(real.startswith(f"{d}/") for d in system):
                self._interpreters[suffix] = real
        probe = [self.bwrap, "--unshare-all", "--die-with-parent"]
        for path in SYSTEM:
            probe += ["--ro-bind-try", path, path]
        probe += ["--", "/bin/true"]
        try:
            result = subprocess.run(probe, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as error:
            detail = str(error)
        else:
            if result.returncode == 0:
                return Status(available=True, reason="", suffixes=list(self._interpreters))
            detail = result.stderr.strip() or f"exit code {result.returncode}"
        return Status(
            available=False,
            reason=f"bubblewrap could not start a sandbox here ({detail[:200]}).",
            suffixes=[],
        )


def _hidden_in(folder: Path) -> list[tuple[str, bool]]:
    """The hidden entries directly in `folder`, to be masked. Links are left alone:
    what they point to is only reachable if it is in the folder anyway."""
    hidden = []
    for entry in os.scandir(folder):
        if entry.name.startswith(".") and not entry.is_symlink():
            hidden.append((entry.path, entry.is_dir(follow_symlinks=False)))
    return hidden


def _limits() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (int(RUN_SECONDS) + 5,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_SIZE_LIMIT,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (OPEN_FILES_LIMIT,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _kill(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def _read(stream: asyncio.StreamReader) -> tuple[str, bool]:
    """Everything `stream` gives, up to MAX_OUTPUT bytes; the rest is read and dropped."""
    kept = bytearray()
    cut = False
    while chunk := await stream.read(65_536):
        room = MAX_OUTPUT - len(kept)
        if len(chunk) > room:
            cut = True
        kept += chunk[: max(room, 0)]
    return kept.decode("utf-8", errors="replace"), cut
