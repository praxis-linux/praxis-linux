"""Read-only access to the folders the user allows.

Every path is resolved (following symlinks) and must land inside an allowed
root. Hidden entries — any name starting with a dot, which covers `.ssh`,
`.env`, `.git` and Praxis's own `.praxis` — are neither listed nor readable
unless hidden access is switched on.
"""

import fnmatch
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

# How much one call may return or walk, whatever is asked for.
MAX_ENTRIES = 200
MAX_SCANNED = 50_000
MAX_READ_BYTES = 64_000

# Large, machine-generated trees that are never what the user is looking for.
SKIPPED_DIRECTORIES = {"node_modules", "__pycache__"}


class AccessError(ValueError):
    """The request names something praxis-fs will not or cannot read."""


class Refused(AccessError):
    """The request names something praxis-fs is not allowed to read, whether or not it exists."""


class Entry(BaseModel):
    name: str
    path: str
    kind: Literal["file", "directory"]
    size: int | None
    modified: datetime


class Listing(BaseModel):
    directory: str
    # Set when the listing is a search result rather than a folder's contents.
    pattern: str | None = None
    entries: list[Entry]
    # How many entries matched; more than len(entries) when the listing was cut short.
    total: int
    truncated: bool


class FileText(BaseModel):
    path: str
    size: int
    content: str
    truncated: bool


def roots_from_env() -> list[Path]:
    """Allowed roots from PRAXIS_FS_ROOTS (os.pathsep-separated); the home folder by default."""
    raw = os.environ.get("PRAXIS_FS_ROOTS", "")
    roots = [Path(part).expanduser() for part in raw.split(os.pathsep) if part.strip()]
    return roots or [Path.home()]


class Files:
    def __init__(self, roots: list[Path], *, allow_hidden: bool = False) -> None:
        if not roots:
            raise ValueError("praxis-fs needs at least one allowed folder")
        self.roots = [root.resolve() for root in roots]
        self.allow_hidden = allow_hidden

    def resolve(self, path: str) -> Path:
        """The real path `path` names, if it is somewhere praxis-fs may read."""
        try:
            candidate = Path(path).expanduser()
            if not candidate.is_absolute():
                candidate = self.roots[0] / candidate
            resolved = candidate.resolve()
        except (OSError, RuntimeError, ValueError) as error:
            raise AccessError(f"{path!r} is not a usable path.") from error
        self._check(resolved, path)
        return resolved

    def _check(self, resolved: Path, asked: str) -> None:
        """Raise unless `resolved`, a real path, is inside a root and not hidden."""
        root = next((root for root in self.roots if resolved.is_relative_to(root)), None)
        if root is None:
            allowed = ", ".join(str(root) for root in self.roots)
            raise Refused(f"{asked} is outside the folders Praxis may read ({allowed}).")
        if not self.allow_hidden and any(
            _hidden(part) for part in resolved.relative_to(root).parts
        ):
            raise Refused(f"{asked} is hidden, and Praxis does not read hidden files or folders.")

    def list_directory(self, path: str = "") -> Listing:
        directory = self._directory(path)
        entries = [entry for child in directory.iterdir() if (entry := self._entry(child))]
        entries.sort(key=lambda e: (e.kind == "file", e.name.lower()))
        return _listing(directory, entries)

    def search_files(
        self, pattern: str = "*", directory: str = "", modified_within_days: int | None = None
    ) -> Listing:
        """Files under `directory`, at any depth, whose name matches `pattern`. Newest first."""
        top = self._directory(directory)
        globs = _globs(pattern)
        since = (
            datetime.now(UTC) - timedelta(days=modified_within_days)
            if modified_within_days is not None
            else None
        )

        matches: list[Entry] = []
        scanned = 0
        for current, directories, names in os.walk(top):
            directories[:] = [d for d in directories if self._descend(d)]
            for name in names:
                scanned += 1
                if any(fnmatch.fnmatch(name.lower(), glob) for glob in globs):
                    entry = self._entry(Path(current) / name)
                    if (
                        entry
                        and entry.kind == "file"
                        and (since is None or entry.modified >= since)
                    ):
                        matches.append(entry)
            if scanned >= MAX_SCANNED:
                break

        matches.sort(key=lambda e: e.modified, reverse=True)
        return _listing(top, matches, pattern=pattern, cut_short=scanned >= MAX_SCANNED)

    def read_text_file(self, path: str, max_bytes: int = 20_000) -> FileText:
        file = self.resolve(path)
        if file.is_dir():
            raise AccessError(f"{path} is a folder, not a file.")
        if not file.is_file():
            raise AccessError(f"There is no file at {path}.")
        limit = max(1, min(max_bytes, MAX_READ_BYTES))
        size = file.stat().st_size
        with file.open("rb") as handle:
            data = handle.read(limit)
        if b"\0" in data:
            raise AccessError(f"{path} is not a text file.")
        return FileText(
            path=str(file),
            size=size,
            content=data.decode("utf-8", errors="replace"),
            truncated=size > limit,
        )

    def _directory(self, path: str) -> Path:
        directory = self.resolve(path)
        if not directory.exists():
            raise AccessError(f"There is no folder at {path}.")
        if not directory.is_dir():
            raise AccessError(f"{path} is a file, not a folder.")
        return directory

    def _descend(self, name: str) -> bool:
        return name not in SKIPPED_DIRECTORIES and (self.allow_hidden or not _hidden(name))

    def _entry(self, path: Path) -> Entry | None:
        if not self.allow_hidden and _hidden(path.name):
            return None
        try:
            # A symlink is reported only if what it points to could be read too.
            self._check(path.resolve(), str(path))
            stat = path.stat()
        except (AccessError, OSError):
            # Points somewhere off limits, is a broken symlink, or is gone since it was listed.
            return None
        is_directory = path.is_dir()
        return Entry(
            name=path.name,
            path=str(path),
            kind="directory" if is_directory else "file",
            size=None if is_directory else stat.st_size,
            modified=datetime.fromtimestamp(stat.st_mtime, UTC),
        )


def _globs(pattern: str) -> list[str]:
    """The file-name globs `pattern` stands for.

    Callers are often small language models, so this reads what they tend to write:
    alternatives separated by commas, bars or spaces; a bare extension ('.pdf'); a
    plain word, matching any name containing it; stray regex anchors.
    """
    globs = []
    for part in re.split(r"[,|;\s]+", pattern.lower()):
        part = part.strip("^$")
        if not part:
            continue
        if any(c in part for c in "*?["):
            globs.append(part)
        elif part.startswith("."):
            globs.append(f"*{part}")
        else:
            globs.append(f"*{part}*")
    return globs or ["*"]


def _hidden(name: str) -> bool:
    return name.startswith(".")


def _listing(
    directory: Path, entries: list[Entry], *, pattern: str | None = None, cut_short: bool = False
) -> Listing:
    return Listing(
        directory=str(directory),
        pattern=pattern,
        entries=entries[:MAX_ENTRIES],
        total=len(entries),
        truncated=cut_short or len(entries) > MAX_ENTRIES,
    )
