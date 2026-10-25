"""Running a file the user picked, inside a bubblewrap sandbox.

What the code can reach: the system's programs and libraries, read-only; a few
files from /etc that programs need to start; a private /tmp it may write to; and
its own folder, which it may read but never change. Where bubblewrap can lay a
throwaway layer over that folder (`--tmp-overlay`, bubblewrap 0.10 and later), the
code may write there too, and what it writes is gone when it ends. Hidden files and
folders anywhere in its folder are masked, and so are sockets.

What it cannot reach: the network, the rest of the home folder, other drives
(/mnt, /media), /run (and with it the user's session and D-Bus), and any process
outside the sandbox. It cannot make user namespaces of its own. A run is stopped
after RUN_SECONDS, and what it prints is cut off after MAX_OUTPUT bytes.

Not limited: memory, and how many processes the code starts. Those need cgroups,
which come with the Phase 1 session.
"""

import asyncio
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

from praxis_fs.fs import SKIPPED_DIRECTORIES, AccessError, Files
from pydantic import BaseModel

RUN_SECONDS = 10.0
MAX_OUTPUT = 64_000
# How many entries of a file's folder are checked for things to mask before it runs.
MAX_CHECKED = 20_000

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


def base_command(bwrap: str, *, disable_userns: bool) -> list[str]:
    """A sandbox with nothing in it but the system, /dev, /proc and an empty /tmp."""
    command = [bwrap, "--unshare-all", "--die-with-parent", "--new-session"]
    if disable_userns:
        command += ["--unshare-user", "--disable-userns"]
    for path in SYSTEM + ETC:
        command += ["--ro-bind-try", path, path]
    return command + ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]


def sandbox_command(
    bwrap: str,
    interpreter: str,
    file: str,
    *,
    masked: list[tuple[str, bool]],
    overlay: bool,
    disable_userns: bool = True,
) -> list[str]:
    """The bwrap command that runs `file` with `interpreter`.

    `masked`: what in the file's folder is hidden from the code, as (path, is_folder).
    `overlay`: whether the code may write in its folder, to a layer that is thrown
    away when it ends; otherwise the folder is read-only.
    """
    folder = os.path.dirname(file)
    command = base_command(bwrap, disable_userns=disable_userns)
    if overlay:
        command += ["--overlay-src", folder, "--tmp-overlay", folder]
    else:
        command += ["--ro-bind", folder, folder]
    for path, is_folder in masked:
        command += ["--tmpfs", path] if is_folder else ["--ro-bind", "/dev/null", path]
    return command + [
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


class Runner:
    def __init__(self, files: Files, *, bwrap: str | None = None) -> None:
        self.files = files
        self.bwrap = bwrap if bwrap is not None else shutil.which("bwrap")
        self._status: Status | None = None
        self._interpreters: dict[str, str] = {}
        self._overlay = False
        self._disable_userns = False

    def status(self) -> Status:
        """Whether files can be run here, and which. Checked once, by starting sandboxes."""
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
        masked = _to_mask(file.parent, hidden=not self.files.allow_hidden)
        if masked is None:
            raise AccessError(
                f"The folder {file.parent.name} holds too many files for Praxis to check "
                "before running something in it. Move the file into a smaller folder."
            )

        command = sandbox_command(
            self.bwrap or "bwrap",
            interpreter,
            str(file),
            masked=masked,
            overlay=self._overlay,
            disable_userns=self._disable_userns,
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
        def unavailable(reason: str) -> Status:
            return Status(available=False, reason=reason, suffixes=[])

        if not sys.platform.startswith("linux"):
            return unavailable("Running code needs Linux.")
        if not self.bwrap:
            return unavailable(
                "Running code needs bubblewrap, which is not installed. "
                "On Ubuntu: sudo apt install bubblewrap"
            )
        system = [os.path.realpath(path) for path in SYSTEM if os.path.isdir(path)]
        # The system folders are shown to the code whole: none of the user's may be in one.
        for place in [*self.files.roots, Path.home()]:
            real = os.path.realpath(place)
            shown = next((d for d in system if real == d or _inside(real, [d])), None)
            if real == "/" or shown:
                return unavailable(
                    f"Running code is turned off: {place} is inside {shown or '/'}, "
                    "which the sandbox shows to the code it runs."
                )

        try:
            usage = subprocess.run(
                [self.bwrap, "--help"], capture_output=True, text=True, timeout=10
            ).stdout
        except (OSError, subprocess.TimeoutExpired) as error:
            return unavailable(f"bubblewrap could not be started ({error}).")
        self._overlay = "--tmp-overlay" in usage
        self._disable_userns = "--disable-userns" in usage

        base = base_command(self.bwrap, disable_userns=self._disable_userns)
        if not _succeeds([*base, "--", "/bin/true"]):
            # Retry without the nested-namespace guard, which some kernels refuse.
            self._disable_userns = False
            base = base_command(self.bwrap, disable_userns=False)
            if not _succeeds([*base, "--", "/bin/true"]):
                return unavailable(
                    "bubblewrap could not start a sandbox here. Some systems only let "
                    "programs with an AppArmor profile create the namespaces it needs."
                )
        # Looked for only where the sandbox can see: not in a virtual environment
        # or the home folder, which may come first on PATH.
        search = [d for d in os.environ.get("PATH", "").split(os.pathsep) if _inside(d, SYSTEM)]
        search = os.pathsep.join([*search, "/usr/local/bin", "/usr/bin", "/bin"])
        for suffix, name in INTERPRETERS.items():
            found = shutil.which(name, path=search)
            # Run as found, so programs that look at the name they were started by
            # work; but only if both the name and what it points to are in the sandbox.
            if (
                found
                and _inside(found, SYSTEM)
                and _inside(os.path.realpath(found), system)
                and _succeeds([*base, "--", found, "--version"])
            ):
                self._interpreters[suffix] = found
        return Status(available=True, reason="", suffixes=list(self._interpreters))


def _inside(path: str, folders: list[str]) -> bool:
    return any(path.startswith(folder.rstrip("/") + "/") for folder in folders)


def _succeeds(command: list[str]) -> bool:
    try:
        return subprocess.run(command, capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _to_mask(folder: Path, *, hidden: bool) -> list[tuple[str, bool]] | None:
    """What in `folder`, at any depth, the code must not see: hidden entries (if
    `hidden`) and sockets, as (path, is_folder). None if there is too much to check.

    Links are left alone: what they point to is only reachable if it is in the
    folder anyway, where it is checked itself.
    """
    masked: list[tuple[str, bool]] = []
    pending = [str(folder)]
    checked = 0
    while pending:
        try:
            with os.scandir(pending.pop()) as entries:
                found = list(entries)
        except OSError:
            continue
        for entry in found:
            checked += 1
            if checked > MAX_CHECKED:
                return None
            if entry.is_symlink():
                continue
            is_folder = entry.is_dir(follow_symlinks=False)
            if hidden and entry.name.startswith("."):
                masked.append((entry.path, is_folder))
            elif is_folder:
                if entry.name not in SKIPPED_DIRECTORIES:
                    pending.append(entry.path)
            elif stat.S_ISSOCK(entry.stat(follow_symlinks=False).st_mode):
                masked.append((entry.path, False))
    return masked


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
