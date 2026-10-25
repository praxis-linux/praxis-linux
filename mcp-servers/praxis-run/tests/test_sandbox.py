import os
import sys
from pathlib import Path

import pytest
from praxis_fs.fs import AccessError, Files

from praxis_run import sandbox
from praxis_run.sandbox import Runner, sandbox_command

# --- The sandbox command, which is the same everywhere ---


def command(*, writable=True, hidden=()):
    return sandbox_command(
        "/usr/bin/bwrap",
        "/usr/bin/python3",
        "/home/me/code/app.py",
        writable=writable,
        hidden=list(hidden),
    )


def mounts(argv, flag):
    return [argv[i + 1] for i, arg in enumerate(argv) if arg == flag]


def test_no_network_and_nothing_shared_with_the_host():
    argv = command()

    assert argv[:4] == ["/usr/bin/bwrap", "--unshare-all", "--die-with-parent", "--new-session"]
    assert "--share-net" not in argv


def test_only_system_folders_and_a_few_etc_files_are_visible():
    argv = command()

    visible = set(mounts(argv, "--ro-bind-try"))
    assert "/usr" in visible and "/etc/ld.so.cache" in visible
    assert not any(path in visible for path in ("/", "/home", "/root", "/mnt", "/media", "/run"))
    assert not any(path in visible for path in ("/etc", "/etc/shadow", "/var"))


def test_the_files_own_folder_is_writable():
    argv = command()

    assert mounts(argv, "--bind") == ["/home/me/code"]
    assert argv[argv.index("--chdir") + 1] == "/home/me/code"


def test_an_allowed_folder_itself_is_only_readable():
    argv = command(writable=False)

    assert mounts(argv, "--bind") == []
    assert "/home/me/code" in mounts(argv, "--ro-bind")


def test_hidden_entries_in_the_folder_are_masked():
    argv = command(hidden=[("/home/me/code/.git", True), ("/home/me/code/.env", False)])

    assert "/home/me/code/.git" in mounts(argv, "--tmpfs")
    at = argv.index("/home/me/code/.env")
    assert argv[at - 2 : at] == ["--ro-bind", "/dev/null"]


def test_the_environment_is_cleared_and_the_file_run_last():
    argv = command()

    assert "--clearenv" in argv
    assert argv[-3:] == ["--", "/usr/bin/python3", "/home/me/code/app.py"]


# --- What the runner says when it cannot run anything ---


@pytest.fixture
def home(tmp_path):
    (tmp_path / "home" / "code").mkdir(parents=True)
    (tmp_path / "home" / "code" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    return tmp_path / "home"


@pytest.fixture
def files(home, tmp_path):
    return Files([home], backups=tmp_path / "backups")


@pytest.mark.skipif(sys.platform.startswith("linux"), reason="checks a machine without Linux")
async def test_running_needs_linux(files):
    status = Runner(files).status()

    assert not status.available
    assert status.reason == "Running code needs Linux."
    with pytest.raises(AccessError, match="needs Linux"):
        await Runner(files).run("code/app.py")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_running_needs_bubblewrap(files):
    status = Runner(files, bwrap="").status()

    assert not status.available
    assert "bubblewrap" in status.reason


# --- Real runs, where bubblewrap works ---


def real_runner(files):
    runner = Runner(files)
    if not runner.status().available:
        if os.environ.get("PRAXIS_RUN_REQUIRE_SANDBOX"):
            pytest.fail(f"the sandbox is required here: {runner.status().reason}")
        pytest.skip(f"cannot run code here: {runner.status().reason}")
    return runner


@pytest.fixture
def anyio_backend():
    return "asyncio"


pytestmark = pytest.mark.anyio


async def test_a_python_file_runs_and_its_output_comes_back(files, home):
    result = await real_runner(files).run("code/app.py")

    assert result.exit_code == 0
    assert result.stdout == "hi\n"
    assert result.command == "python3 app.py"
    assert not result.timed_out


async def test_a_failing_file_reports_its_exit_code_and_errors(files, home):
    (home / "code" / "bad.py").write_text("raise SystemExit('nope')\n", encoding="utf-8")

    result = await real_runner(files).run("code/bad.py")

    assert result.exit_code == 1
    assert "nope" in result.stderr


async def test_there_is_no_network(files, home):
    script = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=2)\n"
        "    print('connected')\n"
        "except OSError:\n"
        "    print('no network')\n"
    )
    (home / "code" / "net.py").write_text(script, encoding="utf-8")

    result = await real_runner(files).run("code/net.py")

    assert result.stdout == "no network\n"


async def test_the_home_folder_and_secrets_are_out_of_sight(files, home):
    (home / "secret.txt").write_text("mine", encoding="utf-8")
    (home / "code" / ".env").write_text("TOKEN=1", encoding="utf-8")
    script = (
        "import os\n"
        f"print(os.path.exists({str(home / 'secret.txt')!r}))\n"
        "try:\n"
        "    print(open('.env').read() != 'TOKEN=1')\n"
        "except OSError:\n"
        "    print(True)\n"
        "print(os.path.exists('/etc/shadow'))\n"
    )
    (home / "code" / "peek.py").write_text(script, encoding="utf-8")

    result = await real_runner(files).run("code/peek.py")

    assert result.stdout.split() == ["False", "True", "False"], result.stderr


async def test_code_can_write_in_its_own_folder_only(files, home):
    script = (
        "open('out.txt', 'w').write('ok')\n"
        "try:\n"
        "    open('/usr/x', 'w')\n"
        "except OSError:\n"
        "    print('read-only')\n"
    )
    (home / "code" / "write.py").write_text(script, encoding="utf-8")

    result = await real_runner(files).run("code/write.py")

    assert (home / "code" / "out.txt").read_text(encoding="utf-8") == "ok"
    assert result.stdout == "read-only\n"


async def test_code_in_an_allowed_folder_itself_cannot_write_there(files, home):
    script = "try:\n    open('out.txt', 'w')\nexcept OSError:\n    print('read-only')\n"
    (home / "top.py").write_text(script, encoding="utf-8")

    result = await real_runner(files).run("top.py")

    assert result.stdout == "read-only\n"
    assert not (home / "out.txt").exists()


async def test_a_run_that_takes_too_long_is_stopped(files, home, monkeypatch):
    monkeypatch.setattr(sandbox, "RUN_SECONDS", 1.0)
    (home / "code" / "slow.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")

    result = await real_runner(files).run("code/slow.py")

    assert result.timed_out
    assert result.exit_code is None
    assert result.seconds < 10


async def test_long_output_is_cut_off(files, home, monkeypatch):
    monkeypatch.setattr(sandbox, "MAX_OUTPUT", 100)
    (home / "code" / "loud.py").write_text("print('x' * 10_000)\n", encoding="utf-8")

    result = await real_runner(files).run("code/loud.py")

    assert result.truncated
    assert len(result.stdout) == 100


async def test_only_known_kinds_of_file_are_run(files, home):
    (home / "code" / "notes.txt").write_text("hello", encoding="utf-8")

    with pytest.raises(AccessError, match="can only run"):
        await real_runner(files).run("code/notes.txt")


async def test_the_same_path_rules_as_reading(files, home, tmp_path):
    (tmp_path / "elsewhere.py").write_text("print(1)", encoding="utf-8")

    with pytest.raises(AccessError, match="outside"):
        await real_runner(files).run(str(Path(tmp_path / "elsewhere.py")))
