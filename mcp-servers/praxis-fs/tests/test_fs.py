import base64
import hashlib
import os
import subprocess
import time
from pathlib import Path

import pytest

from praxis_fs import fs
from praxis_fs.fs import MAX_ENTRIES, AccessError, Files, roots_from_env

DAY = 24 * 60 * 60


def write(path: Path, text: str = "x", *, age_days: float = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    when = time.time() - age_days * DAY
    os.utime(path, (when, when))
    return path


def link(link: Path, target: Path) -> None:
    """A symlink, or for folders on Windows without the symlink privilege, a junction."""
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError:
        if os.name == "nt" and target.is_dir():
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
        else:
            pytest.skip("symlinks are not available here")


@pytest.fixture
def home(tmp_path):
    root = tmp_path / "home"
    write(root / "notes.txt", "hello\nworld\n")
    write(root / "Documents" / "report.md", "# Report", age_days=2)
    write(root / "Documents" / "old.md", "# Old", age_days=40)
    write(root / "code" / "app.py", "print('hi')", age_days=1)
    write(root / "code" / "deep" / "lib.py", "x = 1", age_days=10)
    write(root / "code" / "node_modules" / "pkg" / "index.py", "junk")
    write(root / ".ssh" / "id_ed25519", "PRIVATE KEY")
    write(root / ".env", "SECRET=1")
    write(root / "code" / ".git" / "config.py", "[core]")
    write(tmp_path / "outside" / "secret.txt", "not yours")
    return root


@pytest.fixture
def files(home, tmp_path):
    return Files([home], backups=tmp_path / "backups")


def names(listing):
    return [entry.name for entry in listing.entries]


# --- listing ---------------------------------------------------------------


def test_lists_folders_first_then_files_without_hidden_entries(files, home):
    listing = files.list_directory(str(home))

    assert names(listing) == ["code", "Documents", "notes.txt"]
    assert [entry.kind for entry in listing.entries] == ["directory", "directory", "file"]
    assert listing.total == 3 and not listing.truncated


def test_entries_carry_absolute_path_size_and_time(files, home):
    (entry,) = [e for e in files.list_directory(str(home)).entries if e.name == "notes.txt"]

    assert entry.path == str(home / "notes.txt")
    assert entry.size == len("hello\nworld\n")
    assert entry.modified.tzinfo is not None


def test_relative_paths_start_at_the_first_root(files):
    assert names(files.list_directory("Documents")) == ["old.md", "report.md"]


def test_long_listings_are_cut_short_and_say_so(files, home):
    for i in range(MAX_ENTRIES + 5):
        write(home / "many" / f"f{i:04}.txt")

    listing = files.list_directory("many")

    assert len(listing.entries) == MAX_ENTRIES
    assert listing.total == MAX_ENTRIES + 5 and listing.truncated


def test_listing_a_file_or_a_missing_folder_is_an_error(files):
    with pytest.raises(AccessError, match="is a file, not a folder"):
        files.list_directory("notes.txt")
    with pytest.raises(AccessError, match="There is no folder"):
        files.list_directory("nope")


# --- search ----------------------------------------------------------------


def test_search_finds_files_at_any_depth_newest_first(files):
    listing = files.search_files("*.py")

    assert names(listing) == ["app.py", "lib.py"]
    assert listing.pattern == "*.py"


def test_search_skips_hidden_and_generated_folders(files):
    found = names(files.search_files("*"))

    assert "index.py" not in found  # node_modules
    assert "config.py" not in found  # .git
    assert "id_ed25519" not in found and ".env" not in found


def test_search_can_be_limited_to_recent_files(files):
    assert names(files.search_files("*.md", modified_within_days=7)) == ["report.md"]
    assert names(files.search_files("*.md")) == ["report.md", "old.md"]


def test_a_plain_word_matches_names_containing_it_in_any_case(files):
    assert names(files.search_files("REPORT")) == ["report.md"]


@pytest.mark.parametrize(
    "pattern",
    ["*.md, *.txt", "*.md|*.txt", ".md .txt", "*.md$;^*.txt", "*.docx|.pdf|.odt|.txt|.md"],
)
def test_several_patterns_can_be_given_in_the_ways_models_write_them(files, pattern):
    assert names(files.search_files(pattern)) == ["notes.txt", "report.md", "old.md"]


def test_an_empty_pattern_matches_everything(files):
    assert names(files.search_files("")) == names(files.search_files("*"))


def test_search_is_confined_to_the_folder_given(files):
    assert names(files.search_files("*", directory="Documents")) == ["report.md", "old.md"]


# --- reading ---------------------------------------------------------------


def test_reads_a_text_file(files, home):
    text = files.read_text_file("notes.txt")

    assert text.content == "hello\nworld\n"
    assert text.path == str(home / "notes.txt")
    assert not text.truncated


def test_long_files_are_cut_off_and_say_so(files, home):
    write(home / "big.txt", "a" * 500)

    text = files.read_text_file("big.txt", max_bytes=100)

    assert len(text.content) == 100
    assert text.size == 500 and text.truncated


def test_binary_files_and_folders_are_not_read(files, home):
    (home / "image.bin").write_bytes(b"\x89PNG\x00\x01")

    with pytest.raises(AccessError, match="not a text file"):
        files.read_text_file("image.bin")
    with pytest.raises(AccessError, match="is a folder"):
        files.read_text_file("Documents")
    with pytest.raises(AccessError, match="There is no file"):
        files.read_text_file("missing.txt")


# --- the boundary ----------------------------------------------------------


@pytest.mark.parametrize("path", ["../outside/secret.txt", "Documents/../../outside/secret.txt"])
def test_paths_that_climb_out_of_the_root_are_refused(files, path):
    with pytest.raises(AccessError, match="outside the folders Praxis may read"):
        files.read_text_file(path)


def test_absolute_paths_outside_the_root_are_refused(files, tmp_path):
    with pytest.raises(AccessError, match="outside the folders Praxis may read"):
        files.read_text_file(str(tmp_path / "outside" / "secret.txt"))
    with pytest.raises(AccessError, match="outside the folders Praxis may read"):
        files.list_directory(str(tmp_path))


@pytest.mark.parametrize("path", [".env", ".ssh/id_ed25519", "code/.git/config.py"])
def test_hidden_files_are_refused_even_by_exact_path(files, path):
    with pytest.raises(AccessError, match="hidden"):
        files.read_text_file(path)


def test_hidden_folders_cannot_be_listed_or_searched(files):
    with pytest.raises(AccessError, match="hidden"):
        files.list_directory(".ssh")
    with pytest.raises(AccessError, match="hidden"):
        files.search_files("*", directory=".ssh")


def test_a_linked_folder_outside_the_root_is_neither_followed_nor_listed(files, home, tmp_path):
    link(home / "escape", tmp_path / "outside")

    assert "escape" not in names(files.list_directory(str(home)))
    assert "secret.txt" not in names(files.search_files("*"))
    with pytest.raises(AccessError, match="outside"):
        files.list_directory("escape")
    with pytest.raises(AccessError, match="outside"):
        files.read_text_file("escape/secret.txt")


def test_a_linked_file_outside_the_root_is_neither_read_nor_listed(files, home, tmp_path):
    link(home / "escape.txt", tmp_path / "outside" / "secret.txt")

    assert "escape.txt" not in names(files.list_directory(str(home)))
    assert "escape.txt" not in names(files.search_files("*"))
    with pytest.raises(AccessError, match="outside"):
        files.read_text_file("escape.txt")


def test_a_link_to_a_hidden_folder_is_refused(files, home):
    link(home / "keys", home / ".ssh")

    assert "keys" not in names(files.list_directory(str(home)))
    with pytest.raises(AccessError, match="hidden"):
        files.read_text_file("keys/id_ed25519")


def test_unusable_paths_are_refused_not_crashed_on(files):
    with pytest.raises(AccessError):
        files.read_text_file("bad\0path")


def test_hidden_access_can_be_switched_on(home):
    files = Files([home], allow_hidden=True)

    assert ".env" in names(files.list_directory(str(home)))
    assert files.read_text_file(".env").content == "SECRET=1"


windows_only = pytest.mark.skipif(os.name != "nt", reason="a Windows file system feature")


@windows_only
def test_files_and_folders_windows_marks_hidden_are_refused(files, home):
    write(home / "AppData" / "Roaming" / "token.txt", "TOKEN")
    write(home / "secret.txt", "SECRET")
    subprocess.run(["attrib", "+h", str(home / "AppData")], check=True)
    subprocess.run(["attrib", "+h", str(home / "secret.txt")], check=True)

    assert "AppData" not in names(files.list_directory(str(home)))
    assert "secret.txt" not in names(files.list_directory(str(home)))
    assert "token.txt" not in names(files.search_files("*.txt"))
    with pytest.raises(AccessError, match="hidden"):
        files.read_text_file("AppData/Roaming/token.txt")
    with pytest.raises(AccessError, match="hidden"):
        files.read_text_file("secret.txt")
    with pytest.raises(AccessError, match="hidden"):
        files.list_directory("AppData")


@windows_only
def test_alternate_data_streams_are_refused(files, home):
    (home / "notes.txt:hidden").write_text("ADS DATA", encoding="utf-8")

    with pytest.raises(AccessError, match="hidden part of a file"):
        files.read_text_file("notes.txt:hidden")
    assert files.read_text_file("notes.txt").content == "hello\nworld\n"


def test_a_link_that_loops_back_does_not_make_a_search_run_forever(files, home):
    link(home / "code" / "loop", home)
    link(home / "code" / "loop2", home / "code")

    started = time.monotonic()
    found = files.search_files("app.py")

    assert time.monotonic() - started < 5
    assert names(found) == ["app.py"]
    assert not found.truncated


def test_folders_count_toward_the_scan_cap(files, home, monkeypatch):
    for i in range(30):
        (home / "many" / f"d{i}").mkdir(parents=True)
    monkeypatch.setattr(fs, "MAX_SCANNED", 10)

    found = files.search_files("*", directory="many")

    assert found.truncated


def test_a_search_stops_at_its_time_budget(files, monkeypatch):
    monkeypatch.setattr(fs, "SEARCH_SECONDS", 0.0)

    found = files.search_files("*")

    assert found.truncated
    assert found.total == 0


def test_an_absurd_number_of_days_is_treated_as_all_time(files):
    assert names(files.search_files("*.md", modified_within_days=10**12)) == [
        "report.md",
        "old.md",
    ]


# --- configuration ---------------------------------------------------------


def test_roots_come_from_the_environment(monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv("PRAXIS_FS_ROOTS", os.pathsep.join([str(a), str(b)]))

    assert roots_from_env() == [a, b]


def test_the_home_folder_is_the_default_root(monkeypatch):
    monkeypatch.delenv("PRAXIS_FS_ROOTS", raising=False)

    assert roots_from_env() == [Path.home()]


def test_a_second_root_is_reachable_by_absolute_path(home, tmp_path):
    files = Files([home, tmp_path / "outside"])

    assert files.read_text_file(str(tmp_path / "outside" / "secret.txt")).content == "not yours"


# --- images ----------------------------------------------------------------

# The smallest valid PNG: one transparent pixel.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@pytest.mark.parametrize(
    ("head", "media_type"),
    [
        (PNG, "image/png"),
        (b"\xff\xd8\xff\xe0" + b"\0" * 20, "image/jpeg"),
        (b"GIF89a" + b"\0" * 20, "image/gif"),
        (b"RIFF\0\0\0\0WEBPVP8 " + b"\0" * 20, "image/webp"),
    ],
)
def test_images_are_read_by_what_their_bytes_are(files, home, head, media_type):
    (home / "picture.bin").write_bytes(head)

    image = files.read_image("picture.bin")

    assert image.media_type == media_type
    assert base64.b64decode(image.data) == head
    assert image.size == len(head)


@pytest.mark.parametrize(
    "content",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b"hello, not an image",
        b"",
    ],
)
def test_anything_else_is_not_shown_as_an_image_whatever_its_name(files, home, content):
    (home / "trick.png").write_bytes(content)

    with pytest.raises(AccessError, match="not an image"):
        files.read_image("trick.png")


def test_images_follow_the_same_rules_as_text(files, home):
    (home / ".secret.png").write_bytes(PNG)

    with pytest.raises(AccessError, match="hidden"):
        files.read_image(".secret.png")
    with pytest.raises(AccessError, match="outside"):
        files.read_image("../outside/secret.txt")


def test_very_large_images_are_not_read(files, home, monkeypatch):
    (home / "big.png").write_bytes(PNG)
    monkeypatch.setattr(fs, "MAX_IMAGE_BYTES", 10)

    with pytest.raises(AccessError, match="too large"):
        files.read_image("big.png")


# --- Writing ---


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_a_whole_utf8_file_comes_with_the_hash_needed_to_change_it(files, home):
    assert files.read_text_file("notes.txt").sha256 == sha(b"hello\nworld\n")


def test_a_file_read_only_in_part_cannot_be_changed(files, home):
    write(home / "long.txt", "a" * 100)

    text = files.read_text_file("long.txt", max_bytes=10)

    assert text.truncated
    assert text.sha256 is None


def test_a_file_that_is_not_utf8_cannot_be_changed(files, home):
    (home / "latin1.txt").write_bytes("café".encode("latin-1"))

    assert files.read_text_file("latin1.txt").sha256 is None


def test_changing_a_file(files, home):
    base = files.read_text_file("code/app.py").sha256

    written = files.write_text_file("code/app.py", "print('bye')\n", base)

    assert (home / "code" / "app.py").read_text(encoding="utf-8") == "print('bye')\n"
    assert written.path == str(home / "code" / "app.py")
    assert written.sha256 == sha(b"print('bye')\n")
    assert written.size == 13
    assert not written.created


def test_a_file_changed_since_it_was_read_is_not_overwritten(files, home):
    base = files.read_text_file("code/app.py").sha256
    write(home / "code" / "app.py", "changed elsewhere")

    with pytest.raises(AccessError, match="changed since it was opened"):
        files.write_text_file("code/app.py", "mine", base)
    assert (home / "code" / "app.py").read_text(encoding="utf-8") == "changed elsewhere"


def test_creating_a_file(files, home):
    written = files.write_text_file("code/new.py", "x = 2\n")

    assert (home / "code" / "new.py").read_text(encoding="utf-8") == "x = 2\n"
    assert written.created


def test_creating_never_overwrites(files, home):
    with pytest.raises(AccessError, match="already exists"):
        files.write_text_file("notes.txt", "gone")
    assert (home / "notes.txt").read_text(encoding="utf-8") == "hello\nworld\n"


def test_creating_needs_the_folder_to_exist(files, home):
    with pytest.raises(AccessError, match="no folder"):
        files.write_text_file("nowhere/new.txt", "x")
    assert not (home / "nowhere").exists()


@pytest.mark.parametrize("path", [".env", ".ssh/config", "code/.git/hooks.py", "../outside/x.txt"])
def test_writes_follow_the_same_rules_as_reads(files, home, path):
    with pytest.raises(AccessError, match="hidden|outside"):
        files.write_text_file(path, "x")
    assert (home / ".env").read_text(encoding="utf-8") == "SECRET=1"


def test_crlf_line_endings_are_kept(files, home):
    (home / "dos.txt").write_bytes(b"one\r\ntwo\r\n")
    base = files.read_text_file("dos.txt").sha256

    files.write_text_file("dos.txt", "one\ntwo\nthree\n", base)

    assert (home / "dos.txt").read_bytes() == b"one\r\ntwo\r\nthree\r\n"


@pytest.mark.skipif(os.name == "nt", reason="no executable bit on Windows")
def test_an_executable_file_stays_executable(files, home):
    script = write(home / "run.sh", "echo hi\n")
    script.chmod(0o755)

    files.write_text_file("run.sh", "echo bye\n", files.read_text_file("run.sh").sha256)

    assert script.stat().st_mode & 0o777 == 0o755


def test_too_much_text_is_not_written(files, home, monkeypatch):
    monkeypatch.setattr(fs, "MAX_WRITE_BYTES", 5)

    with pytest.raises(AccessError, match="too much"):
        files.write_text_file("code/big.txt", "123456")


def test_a_write_leaves_no_temporary_file_behind(files, home):
    files.write_text_file("code/app.py", "x", files.read_text_file("code/app.py").sha256)

    assert sorted(p.name for p in (home / "code").iterdir()) == [
        ".git",
        "app.py",
        "deep",
        "node_modules",
    ]


# --- Undoing ---


def test_undoing_a_change_puts_the_file_back(files, home):
    written = files.write_text_file(
        "code/app.py", "print('bye')", files.read_text_file("code/app.py").sha256
    )

    undone = files.undo_write(written.backup)

    assert (home / "code" / "app.py").read_text(encoding="utf-8") == "print('hi')"
    assert undone.path == str(home / "code" / "app.py")
    assert not undone.removed


def test_undoing_a_creation_removes_the_file(files, home):
    written = files.write_text_file("code/new.py", "x = 2\n")

    assert files.undo_write(written.backup).removed
    assert not (home / "code" / "new.py").exists()


def test_a_change_is_undone_once(files, home):
    written = files.write_text_file("code/new.py", "x = 2\n")
    files.undo_write(written.backup)

    with pytest.raises(AccessError, match="nothing|no change"):
        files.undo_write(written.backup)


def test_nothing_is_undone_over_a_later_change(files, home):
    written = files.write_text_file(
        "code/app.py", "mine", files.read_text_file("code/app.py").sha256
    )
    write(home / "code" / "app.py", "changed again")

    with pytest.raises(AccessError, match="changed since"):
        files.undo_write(written.backup)
    assert (home / "code" / "app.py").read_text(encoding="utf-8") == "changed again"


@pytest.mark.parametrize("backup", ["../../outside", "", "20260101T000000-zzzzzzzz", "x/y"])
def test_an_undo_id_cannot_name_anything_else(files, backup):
    with pytest.raises(AccessError, match="no change"):
        files.undo_write(backup)


def test_backups_are_kept_where_they_were_asked_to_be(files, home, tmp_path):
    written = files.write_text_file(
        "code/app.py", "mine", files.read_text_file("code/app.py").sha256
    )

    kept = tmp_path / "backups" / written.backup
    assert (kept / "original").read_text(encoding="utf-8") == "print('hi')"


def test_backups_default_to_the_praxis_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("PRAXIS_FS_BACKUPS", raising=False)
    assert fs.backups_from_env() == Path.home() / ".praxis" / "backups"
    monkeypatch.setenv("PRAXIS_FS_BACKUPS", str(tmp_path))
    assert fs.backups_from_env() == tmp_path


# --- Writing, when something goes wrong or happens meanwhile ---


def test_a_file_that_appears_while_being_created_is_left_alone(files, home, monkeypatch, tmp_path):
    back_up = Files._back_up

    def meanwhile(self, file, old, written):
        backup = back_up(self, file, old, written)
        file.write_text("someone else's", encoding="utf-8")
        return backup

    monkeypatch.setattr(Files, "_back_up", meanwhile)

    with pytest.raises(AccessError, match="appeared while it was being created"):
        files.write_text_file("code/new.py", "mine")
    assert (home / "code" / "new.py").read_text(encoding="utf-8") == "someone else's"
    # Nothing was written, so nothing is left to undo.
    assert list((tmp_path / "backups").iterdir()) == []


def test_a_write_that_fails_leaves_no_backup_and_says_why(files, home, monkeypatch, tmp_path):
    def broken(file, data, *, create):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(fs, "_put", broken)

    with pytest.raises(AccessError, match=r"could not be written \(Permission denied\)"):
        files.write_text_file("code/app.py", "x", files.read_text_file("code/app.py").sha256)
    assert list((tmp_path / "backups").iterdir()) == []
    assert (home / "code" / "app.py").read_text(encoding="utf-8") == "print('hi')"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_a_new_file_gets_the_usual_permissions_and_backups_are_private(files, home, tmp_path):
    files.write_text_file("code/new.py", "x")

    umask = os.umask(0)
    os.umask(umask)
    assert (home / "code" / "new.py").stat().st_mode & 0o777 == 0o666 & ~umask
    assert (tmp_path / "backups").stat().st_mode & 0o777 == 0o700


def test_old_backups_are_forgotten(files, home, tmp_path):
    old = tmp_path / "backups" / "20000101T000000-0a1b2c3d"
    old.mkdir(parents=True)
    (old / "write.json").write_text("{}", encoding="utf-8")

    files.write_text_file("code/new.py", "x")

    assert not old.exists()
    assert len(list((tmp_path / "backups").iterdir())) == 1
