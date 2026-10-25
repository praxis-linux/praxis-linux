import os
import sys
from pathlib import Path

import pytest
from praxis_fs.fs import AccessError, Files

from praxis_run import sandbox
from praxis_run.sandbox import Runner, sandbox_command

# --- The sandbox command, which is the same everywhere ---


def command(*, overlay=False, masked=(), disable_userns=True):
    return sandbox_command(
        "/usr/bin/bwrap",
        "/usr/bin/python3",
        "/home/me/code/app.py",
        masked=list(masked),
        overlay=overlay,
        disable_userns=disable_userns,
    )


def mounts(argv, flag):
    return [argv[i + 1] for i, arg in enumerate(argv) if arg == flag]


def test_no_network_nothing_shared_with_the_host_and_no_namespaces_of_its_own():
    argv = command()

    assert argv[:4] == ["/usr/bin/bwrap", "--unshare-all", "--die-with-parent", "--new-session"]
    assert "--share-net" not in argv
    assert argv[4:6] == ["--unshare-user", "--disable-userns"]
    assert "--disable-userns" not in command(disable_userns=False)


def test_only_system_folders_and_a_few_etc_files_are_visible():
    argv = command()

    visible = set(mounts(argv, "--ro-bind-try"))
    assert "/usr" in visible and "/etc/ld.so.cache" in visible
    assert not any(path in visible for path in ("/", "/home", "/root", "/mnt", "/media", "/run"))
    assert not any(path in visible for path in ("/etc", "/etc/shadow", "/var"))


def test_the_files_own_folder_is_never_writable():
    argv = command()

    assert "--bind" not in argv
    assert "/home/me/code" in mounts(argv, "--ro-bind")
    assert argv[argv.index("--chdir") + 1] == "/home/me/code"


def test_with_an_overlay_writes_go_to_a_layer_that_is_thrown_away():
    argv = command(overlay=True)

    assert mounts(argv, "--overlay-src") == ["/home/me/code"]
    assert mounts(argv, "--tmp-overlay") == ["/home/me/code"]
    assert "--bind" not in argv


def test_what_is_masked_is_masked_after_the_folder_is_mounted():
    argv = command(masked=[("/home/me/code/sub/.git", True), ("/home/me/code/.env", False)])

    assert "/home/me/code/sub/.git" in mounts(argv, "--tmpfs")
    at = argv.index("/home/me/code/.env")
    assert argv[at - 2 : at] == ["--ro-bind", "/dev/null"]
    # After the folder itself is mounted, or the folder would cover the masks.
    assert argv.index("/home/me/code/sub/.git") > argv.index("/home/me/code")


def test_the_environment_is_cleared_and_the_file_run_last():
    argv = command()

    assert "--clearenv" in argv
    assert argv[-3:] == ["--", "/usr/bin/python3", "/home/me/code/app.py"]


def test_hidden_entries_and_sockets_are_found_at_any_depth(tmp_path):
    (tmp_path / "sub" / ".git").mkdir(parents=True)
    (tmp_path / "sub" / ".env").write_text("x", encoding="utf-8")
    (tmp_path / "sub" / "deeper").mkdir()
    (tmp_path / "sub" / "deeper" / ".secret").write_text("x", encoding="utf-8")
    (tmp_path / "node_modules" / ".bin").mkdir(parents=True)
    (tmp_path / "plain.txt").write_text("x", encoding="utf-8")

    masked = sandbox._to_mask(tmp_path, hidden=True)

    assert sorted((Path(p).relative_to(tmp_path).as_posix(), d) for p, d in masked) == [
        ("sub/.env", False),
        ("sub/.git", True),
        ("sub/deeper/.secret", False),
    ]
    assert sandbox._to_mask(tmp_path, hidden=False) == []


def test_a_folder_too_large_to_check_is_not_run_in(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "MAX_CHECKED", 2)
    for name in "abc":
        (tmp_path / name).write_text("x", encoding="utf-8")

    assert sandbox._to_mask(tmp_path, hidden=True) is None


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


async def test_code_cannot_change_its_folder(files, home):
    script = (
        "try:\n"
        "    open('out.txt', 'w').write('ok')\n"
        "    open('app.py', 'a').write('# changed')\n"
        "    print('written, to a layer that is thrown away')\n"
        "except OSError:\n"
        "    print('read-only')\n"
        "open('/tmp/scratch', 'w').write('fine')\n"
    )
    (home / "code" / "write.py").write_text(script, encoding="utf-8")

    result = await real_runner(files).run("code/write.py")

    assert result.exit_code == 0, result.stderr
    assert not (home / "code" / "out.txt").exists()
    assert (home / "code" / "app.py").read_text(encoding="utf-8") == "print('hi')\n"


async def test_hidden_files_deeper_in_the_folder_and_sockets_are_out_of_reach(files, home):
    import socket

    runner = real_runner(files)
    (home / "code" / "sub").mkdir()
    (home / "code" / "sub" / ".env").write_text("TOKEN=1", encoding="utf-8")
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(home / "code" / "agent.sock"))
    server.listen()
    script = (
        "import socket\n"
        "try:\n"
        "    print(open('sub/.env').read() != 'TOKEN=1')\n"
        "except OSError:\n"
        "    print(True)\n"
        "try:\n"
        "    socket.socket(socket.AF_UNIX).connect('agent.sock')\n"
        "    print('connected')\n"
        "except OSError:\n"
        "    print('no socket')\n"
    )
    (home / "code" / "peek2.py").write_text(script, encoding="utf-8")

    try:
        result = await runner.run("code/peek2.py")
    finally:
        server.close()

    assert result.stdout.split() == ["True", "no", "socket"], result.stderr


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_nothing_runs_when_an_allowed_folder_is_inside_a_system_folder(tmp_path):
    runner = Runner(Files([Path("/opt/praxis-work")], backups=tmp_path / "backups"))

    status = runner.status()

    assert not status.available
    assert "/opt/praxis-work is inside /opt" in status.reason


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
