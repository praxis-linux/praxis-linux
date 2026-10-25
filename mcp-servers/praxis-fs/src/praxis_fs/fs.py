"""Read-only access to the folders the user allows.

Every path is resolved (following symlinks) and must land inside an allowed
root. Hidden entries are neither listed nor readable unless hidden access is
switched on: any name starting with a dot, which covers `.ssh`, `.env`, `.git`
and Praxis's own `.praxis`, and on Windows anything with the hidden or system
attribute, which covers `AppData`.
"""

import base64
import fnmatch
import os
import re
import stat
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

# How much one call may return or walk, whatever is asked for.
MAX_ENTRIES = 200
MAX_SCANNED = 50_000
MAX_READ_BYTES = 64_000
MAX_IMAGE_BYTES = 8_000_000
SEARCH_SECONDS = 10.0
MAX_DAYS = 36_500

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


class ImageFile(BaseModel):
    path: str
    size: int
    media_type: Literal["image/png", "image/jpeg", "image/gif", "image/webp"]
    # The file's bytes, base64-encoded.
    data: str


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
        relative = resolved.relative_to(root)
        # On Windows "name:stream" opens an alternate data stream, a second file
        # hidden inside a visible one.
        if os.name == "nt" and any(":" in part for part in relative.parts):
            raise Refused(f"{asked} names a hidden part of a file, which Praxis does not read.")
        if self.allow_hidden:
            return
        hidden = f"{asked} is hidden, and Praxis does not read hidden files or folders."
        if any(_hidden(part) for part in relative.parts):
            raise Refused(hidden)
        below = root
        for part in relative.parts:
            below = below / part
            if _hidden_by_attribute(below):
                raise Refused(hidden)

    def list_directory(self, path: str = "") -> Listing:
        directory = self._directory(path)
        entries = [entry for child in directory.iterdir() if (entry := self._entry(child))]
        entries.sort(key=lambda e: (e.kind == "file", e.name.lower()))
        return _listing(directory, entries)

    def search_files(
        self, pattern: str = "*", directory: str = "", modified_within_days: int | None = None
    ) -> Listing:
        """Files under `directory`, at any depth, whose name matches `pattern`. Newest first.

        The search never follows a symlink or junction into a folder, so a link that
        loops back cannot make it run forever; and it stops after MAX_SCANNED entries
        or SEARCH_SECONDS, saying it was cut short.
        """
        top = self._directory(directory)
        globs = _globs(pattern)
        since = (
            datetime.now(UTC) - timedelta(days=min(max(modified_within_days, 0), MAX_DAYS))
            if modified_within_days is not None
            else None
        )

        matches: list[Entry] = []
        scanned = 0
        cut_short = False
        deadline = time.monotonic() + SEARCH_SECONDS
        pending = [str(top)]
        while pending and not cut_short:
            try:
                with os.scandir(pending.pop()) as children:
                    found = list(children)
            except OSError:
                continue
            for child in found:
                scanned += 1
                if scanned > MAX_SCANNED or time.monotonic() > deadline:
                    cut_short = True
                    break
                if child.is_dir(follow_symlinks=False):
                    if not child.is_symlink() and not child.is_junction() and self._descend(child):
                        pending.append(child.path)
                elif any(fnmatch.fnmatch(child.name.lower(), glob) for glob in globs):
                    entry = self._entry(Path(child.path))
                    if (
                        entry
                        and entry.kind == "file"
                        and (since is None or entry.modified >= since)
                    ):
                        matches.append(entry)

        matches.sort(key=lambda e: e.modified, reverse=True)
        return _listing(top, matches, pattern=pattern, cut_short=cut_short)

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

    def read_image(self, path: str) -> ImageFile:
        """An image file's bytes, for showing it. Only PNG, JPEG, GIF and WebP, judged by
        the bytes themselves, not the file's name; never SVG, which can carry script."""
        file = self.resolve(path)
        if file.is_dir():
            raise AccessError(f"{path} is a folder, not a file.")
        if not file.is_file():
            raise AccessError(f"There is no file at {path}.")
        size = file.stat().st_size
        if size > MAX_IMAGE_BYTES:
            raise AccessError(f"{path} is too large to show ({size // 1_000_000} MB).")
        data = file.read_bytes()
        media_type = _image_type(data[:16])
        if media_type is None:
            raise AccessError(f"{path} is not an image Praxis can show.")
        return ImageFile(
            path=str(file),
            size=size,
            media_type=media_type,
            data=base64.b64encode(data).decode("ascii"),
        )

    def _directory(self, path: str) -> Path:
        directory = self.resolve(path)
        if not directory.exists():
            raise AccessError(f"There is no folder at {path}.")
        if not directory.is_dir():
            raise AccessError(f"{path} is a file, not a folder.")
        return directory

    def _descend(self, folder: os.DirEntry[str]) -> bool:
        if folder.name in SKIPPED_DIRECTORIES:
            return False
        return self.allow_hidden or not (
            _hidden(folder.name) or _hidden_by_attribute(Path(folder.path))
        )

    def _entry(self, path: Path) -> Entry | None:
        """`path`, found inside a folder already allowed, if it may be shown."""
        if not self.allow_hidden and (_hidden(path.name) or _hidden_by_attribute(path)):
            return None
        try:
            resolved = path.resolve()
            # A symlink or junction is shown only if what it points to could be read too.
            if resolved != path:
                self._check(resolved, str(path))
            info = path.stat()
        except (AccessError, OSError):
            # Points somewhere off limits, is a broken link, or is gone since it was listed.
            return None
        is_directory = path.is_dir()
        return Entry(
            name=path.name,
            path=str(path),
            kind="directory" if is_directory else "file",
            size=None if is_directory else info.st_size,
            modified=datetime.fromtimestamp(info.st_mtime, UTC),
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


def _image_type(head: bytes) -> str | None:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def _hidden(name: str) -> bool:
    return name.startswith(".")


_HIDDEN_ATTRIBUTES = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0) | getattr(
    stat, "FILE_ATTRIBUTE_SYSTEM", 0
)


def _hidden_by_attribute(path: Path) -> bool:
    """Whether Windows marks `path` itself hidden or as a system file. Never on other systems."""
    if os.name != "nt":
        return False
    try:
        attributes = os.stat(path, follow_symlinks=False).st_file_attributes
    except (OSError, ValueError):
        return False
    return bool(attributes & _HIDDEN_ATTRIBUTES)


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
