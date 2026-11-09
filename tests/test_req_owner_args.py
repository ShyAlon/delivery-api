"""req-owner.sh argument assembly must survive macOS /bin/bash (3.2) set -u.

2026-11-09: Shy's bare `req debug` died at req-owner.sh line 52 with
"args[@]: unbound variable" - bash 3.2 (still /bin/bash on macOS) treats the
expansion of an EMPTY array as an unbound variable under set -u, so any
command whose args array can stay empty dies before exec. The guarded idiom
${args[@]+"${args[@]}"} is the documented bash 3.2-safe form.

The sandbox ships bash 5.1, which cannot reproduce the 3.2 error mode (the
4.4 behavior change is not gated by any compat option) - so the fail-first
leg is the static invariant below (zero unguarded array expansions), and the
end-to-end legs prove the plumbing under the script's own set -u.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "ops" / "req-owner.sh"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def test_no_array_expansion_is_unsafe_on_an_empty_array_under_set_u():
    """Bash 3.2 set -u kills every unguarded "${name[@]}" on an empty array."""
    import re
    text = SCRIPT.read_text()
    bare = re.findall(r'(?<!\+)"\$\{\w+\[@\]\}"', text)
    assert bare == [], f"unguarded empty-array expansions die under macOS bash 3.2 set -u: {bare}"


@pytest.fixture
def stub_repo(tmp_path):
    repo = tmp_path / "repo"
    ops = repo / "scripts" / "ops"
    ops.mkdir(parents=True)
    shutil.copy(SCRIPT, ops / "req-owner.sh")
    shutil.copy(REPO / "scripts" / "ops" / "terminal-log.sh", ops / "terminal-log.sh")
    (ops / "venv_check.py").write_text("pass\n")
    (ops / "preview-debug.py").write_text("pass\n")
    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text(
        '#!/usr/bin/env bash\n'
        'case "${1:-}" in\n'
        '  -c) exit 0;;\n'
        '  *venv_check.py) exit 0;;\n'
        '  *) echo "EXEC $*";;\n'
        'esac\n')
    (venv_bin / "python").chmod(0o755)
    return repo


def _run(repo, *args):
    return subprocess.run(["bash", str(repo / "scripts" / "ops" / "req-owner.sh"), *args],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)


def test_bare_debug_execs_with_zero_arguments(stub_repo):
    out = _run(stub_repo, "debug")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "unbound variable" not in out.stderr
    exec_line = [l for l in out.stdout.splitlines() if l.startswith("EXEC ")]
    assert exec_line and exec_line[0].endswith("preview-debug.py"), out.stdout
    assert "--run-dir" not in exec_line[0]


def test_debug_run_dir_passes_the_named_run_through(stub_repo):
    out = _run(stub_repo, "debug", "--run-dir", "413a76bd7770f2da551e65a7")
    assert out.returncode == 0, out.stdout + out.stderr
    exec_line = [l for l in out.stdout.splitlines() if l.startswith("EXEC ")]
    assert exec_line and exec_line[0].endswith("preview-debug.py --run-dir 413a76bd7770f2da551e65a7"), out.stdout
