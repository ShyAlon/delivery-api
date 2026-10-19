"""`ops <name>` runs the repo's operations scripts from any folder; `ops install` links it onto PATH after a yes."""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1] / "scripts" / "ops"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture()
def home(tmp_path):
    ops = tmp_path / "checkout" / "scripts" / "ops"
    ops.mkdir(parents=True)
    shutil.copy(OPS / "ops", ops / "ops")
    (ops / "ops").chmod(0o755)
    (ops / "hello.sh").write_text('echo "hello from $(basename "$0") args=[$*] cwd=$(pwd)"\n')
    (ops / "lib.sh").write_text("echo lib-should-not-run\n")
    bindir = tmp_path / "home" / ".local" / "bin"
    bindir.mkdir(parents=True)
    return tmp_path, ops, bindir


def _run(args, cwd, path, stdin=""):
    env = {**os.environ, "PATH": path, "HOME": str(cwd / "home")}
    return subprocess.run(args, cwd=cwd, env=env, input=stdin, capture_output=True, text=True, timeout=30)


def test_the_launcher_is_executable_in_the_repo():
    assert (OPS / "ops").stat().st_mode & stat.S_IXUSR


def test_listing_names_the_scripts_and_hides_the_shared_library(home):
    root, ops, _ = home
    r = _run([str(ops / "ops")], root, os.environ["PATH"])
    assert r.returncode == 0 and "ops hello" in r.stdout and "ops install" in r.stdout and "ops lib" not in r.stdout


def test_an_unknown_name_aborts_with_fix_and_then_run_and_never_runs_the_library(home):
    root, ops, _ = home
    for name in ("nope", "lib"):
        r = _run([str(ops / "ops"), name], root, os.environ["PATH"])
        assert r.returncode == 1 and "Fix:" in r.stdout and "Then run: ops" in r.stdout and "lib-should-not-run" not in r.stdout


def test_install_needs_a_typed_yes_then_works_from_any_folder_through_the_symlink(home):
    root, ops, bindir = home
    path = f"{bindir}:{os.environ['PATH']}"
    r = _run([str(ops / "ops"), "install"], root, path, stdin="no\n")
    assert r.returncode == 1 and not (bindir / "ops").exists()
    r = _run([str(ops / "ops"), "install"], root, path, stdin="yes\n")
    assert r.returncode == 0 and (bindir / "ops").is_symlink()
    elsewhere = root / "somewhere"
    elsewhere.mkdir()
    r = _run(["ops", "hello", "a", "b"], elsewhere, path)
    assert r.returncode == 0 and "hello from hello.sh args=[a b]" in r.stdout


def test_install_with_the_yes_flag_skips_the_question(home):
    root, ops, bindir = home
    r = _run([str(ops / "ops"), "install", "--yes"], root, f"{bindir}:{os.environ['PATH']}")
    assert r.returncode == 0 and (bindir / "ops").is_symlink()


def test_install_without_a_writable_folder_on_path_aborts_and_writes_nothing(home):
    root, ops, bindir = home
    r = _run([str(ops / "ops"), "install", "--yes"], root, "/usr/bin:/bin")
    assert r.returncode == 1 and "Fix:" in r.stdout and "Then run:" in r.stdout and not (bindir / "ops").exists()
