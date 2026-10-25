"""Access to the folders the user allows: reading, and writing text files.

Every path is resolved (following symlinks) and must land inside an allowed
root. Hidden entries are neither listed nor readable unless hidden access is
switched on: any name starting with a dot, which covers `.ssh`, `.env`, `.git`
and Praxis's own `.praxis`, and on Windows anything with the hidden or system
attribute, which covers `AppData`.

A write never happens blind: changing a file needs the hash it had when it was
read, and every write keeps what it replaced, so it can be undone. Whether the
user agreed to a write is the caller's business; see the agent.
"""

import base64
import fnmatch
import hashlib
import json
import os
import re
import secrets
import shutil
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
MAX_WRITE_BYTES = 1_000_000
# How long a write can be undone.
BACKUP_DAYS = 30
SEARCH_SECONDS = 10.0
MAX_DAYS = 36_500

# What undo_write accepts as a backup id: what _back_up makes, and nothing that
# could name a path.
_BACKUP_ID = re.compile(r"\d{8}T\d{6}-[0-9a-f]{8}")

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
    # Set when the whole file was read and is UTF-8 text: the hash write_text_file
    # needs to change it.
    sha256: str | None = None


class ImageFile(BaseModel):
    path: str
    size: int
    media_type: Literal["image/png", "image/jpeg", "image/gif", "image/webp"]
    # The file's bytes, base64-encoded.
    data: str


class Written(BaseModel):
    path: str
    size: int
    sha256: str
    # True when the file did not exist before.
    created: bool
    # What undo_write takes to put things back as they were.
    backup: str


class Undone(BaseModel):
    path: str
    # True when the write being undone had created the file, which is now gone again.
    removed: bool


def roots_from_env() -> list[Path]:
    """Allowed roots from PRAXIS_FS_ROOTS (os.pathsep-separated); the home folder by default."""
    raw = os.environ.get("PRAXIS_FS_ROOTS", "")
    roots = [Path(part).expanduser() for part in raw.split(os.pathsep) if part.strip()]
    return roots or [Path.home()]


def backups_from_env() -> Path:
    """Where writes keep what they replaced: PRAXIS_FS_BACKUPS, or ~/.praxis/backups."""
    raw = os.environ.get("PRAXIS_FS_BACKUPS", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".praxis" / "backups"


class Files:
    def __init__(
        self, roots: list[Path], *, allow_hidden: bool = False, backups: Path | None = None
    ) -> None:
        if not roots:
            raise ValueError("praxis-fs needs at least one allowed folder")
        self.roots = [root.resolve() for root in roots]
        self.allow_hidden = allow_hidden
        self.backups = backups if backups is not None else backups_from_env()

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
        whole = size <= limit and len(data) == size
        return FileText(
            path=str(file),
            size=size,
            content=data.decode("utf-8", errors="replace"),
            truncated=not whole,
            sha256=_sha256(data) if whole and _is_utf8(data) else None,
        )

    def write_text_file(
        self, path: str, content: str, expected_sha256: str | None = None
    ) -> Written:
        """Write `content` to the file at `path`, keeping what it replaces.

        With `expected_sha256` the file must exist and still have that hash; without
        it the file must not exist yet, and is created. A file that used CRLF line
        endings keeps them.
        """
        file = self.resolve(path)
        data = content.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            raise AccessError(f"That is too much text to write ({len(data) // 1000} KB).")
        if file.is_dir():
            raise AccessError(f"{path} is a folder, not a file.")
        if expected_sha256 is None:
            if file.exists():
                raise AccessError(f"{path} already exists, so it was not overwritten.")
            if not file.parent.is_dir():
                raise AccessError(f"There is no folder to put {path} in.")
            old = None
        else:
            if not file.is_file():
                raise AccessError(f"There is no file at {path} any more.")
            old = file.read_bytes()
            if _sha256(old) != expected_sha256:
                raise AccessError(f"{path} has changed since it was opened, so it was not changed.")
            if b"\r\n" in old and b"\r" not in data:
                data = data.replace(b"\n", b"\r\n")
        written = _sha256(data)
        try:
            backup = self._back_up(file, old, written)
        except OSError as error:
            raise AccessError(
                f"No copy of {path} could be kept to undo with, so it was not changed."
            ) from error
        try:
            _put(file, data, create=old is None)
        except BaseException as error:
            # Nothing was written, so there is nothing to undo.
            shutil.rmtree(self.backups / backup, ignore_errors=True)
            if isinstance(error, OSError):
                raise AccessError(f"{path} could not be written ({error.strerror}).") from error
            raise
        return Written(
            path=str(file), size=len(data), sha256=written, created=old is None, backup=backup
        )

    def undo_write(self, backup: str) -> Undone:
        """Put a file back as it was before the write that made `backup`.

        Refused if the file has changed since that write. A backup is used once.
        """
        folder = self.backups / backup
        try:
            if not _BACKUP_ID.fullmatch(backup):
                raise OSError
            record = json.loads((folder / "write.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise AccessError("There is no change with that id to undo.") from error
        file = self.resolve(record["path"])
        current = file.read_bytes() if file.is_file() else None
        if current is None or _sha256(current) != record["sha256"]:
            raise AccessError(f"{record['path']} has changed since, so the change was not undone.")
        if record["created"]:
            file.unlink()
        else:
            _put(file, (folder / "original").read_bytes(), create=False)
        shutil.rmtree(folder, ignore_errors=True)
        return Undone(path=str(file), removed=record["created"])

    def _back_up(self, file: Path, old: bytes | None, written: str) -> str:
        """Keep `old`, what `file` held before a write, under a new backup id."""
        # Backups hold old versions of the user's files: only the user may read them.
        self.backups.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.backups.mkdir(exist_ok=True, mode=0o700)
        self._prune()
        backup = f"{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(4)}"
        folder = self.backups / backup
        folder.mkdir(mode=0o700)
        if old is not None:
            (folder / "original").write_bytes(old)
        record = {"path": str(file), "sha256": written, "created": old is None}
        (folder / "write.json").write_text(json.dumps(record), encoding="utf-8")
        return backup

    def _prune(self) -> None:
        """Forget backups older than BACKUP_DAYS: they keep old file contents, secrets too."""
        oldest = time.strftime("%Y%m%dT%H%M%S", time.localtime(time.time() - BACKUP_DAYS * 86_400))
        for folder in self.backups.iterdir():
            if _BACKUP_ID.fullmatch(folder.name) and folder.name < oldest:
                shutil.rmtree(folder, ignore_errors=True)

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


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


# What a new file's permissions are cut down by, as for any other program. Read once.
_UMASK = os.umask(0o022)
os.umask(_UMASK)

_EXCLUSIVE = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
)


def _put(file: Path, data: bytes, *, create: bool) -> None:
    """Make `file` hold `data` in one step: a reader sees the old file or the new, never half.

    With `create` the file must not exist, and one that appears meanwhile is left
    alone: a rename would overwrite it, a link does not.
    """
    temporary = file.with_name(f".{file.name}.praxis-{secrets.token_hex(8)}")
    # Private until it is in place, since the text may be secret; never through a link.
    descriptor = os.open(temporary, _EXCLUSIVE, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if not create:
            shutil.copymode(file, temporary)
            os.replace(temporary, file)
            return
        os.chmod(temporary, 0o666 & ~_UMASK)
        try:
            os.link(temporary, file)
        except FileExistsError as error:
            raise AccessError(
                f"{file.name} appeared while it was being created, so it was not overwritten."
            ) from error
        except OSError:
            # No hard links here (some network and FAT file systems): create it in place.
            _create_in_place(file, data)
        temporary.unlink()
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _create_in_place(file: Path, data: bytes) -> None:
    try:
        descriptor = os.open(file, _EXCLUSIVE, 0o666)
    except FileExistsError as error:
        raise AccessError(
            f"{file.name} appeared while it was being created, so it was not overwritten."
        ) from error
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


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
