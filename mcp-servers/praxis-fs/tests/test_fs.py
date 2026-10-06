import os
import time
from pathlib import Path

import pytest

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
def files(home):
    return Files([home])


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
