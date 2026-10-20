"""The server operation scripts live in the repo and bring themselves up to date.

Hermetic: a throwaway bare repository stands in for origin; nothing touches a server.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1] / "scripts" / "ops"
NAMES = ("deploy-full.sh", "go-live.sh", "grant-credits.sh", "lib.sh", "preview.sh", "req-install.sh")
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git")


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}).stdout.strip()


@pytest.fixture()
def clone(tmp_path):
    bare = tmp_path / "ShyAlon" / "delivery-api.git"
    bare.parent.mkdir()
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(bare), str(seed))
    (seed / "scripts" / "ops").mkdir(parents=True)
    for n in NAMES:
        shutil.copy(OPS / n, seed / "scripts" / "ops" / n)
    (seed / "scripts" / "ops" / "probe.sh").write_text('. "$(dirname "$0")/lib.sh"; ops_start probe "$0"; echo STARTED\n')
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "ops")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-q", "origin", "main")
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(bare), str(work))
    return seed, work


def _probe(work):
    """Run only the shared start (lib.sh) through a stub script, never the server part."""
    return subprocess.run(["bash", "scripts/ops/probe.sh"], cwd=work, capture_output=True, text=True)


def test_scripts_parse():
    for n in NAMES:
        assert subprocess.run(["bash", "-n", str(OPS / n)]).returncode == 0, n


def test_no_script_asks_for_arguments_or_a_stamp():
    for n in NAMES:
        text = (OPS / n).read_text()
        assert "REQUI-SCRIPT" not in text and "run req" not in text.lower() and "then run: req" not in text.lower(), n


def test_go_live_wording_does_not_claim_nobody_is_admitted():
    assert "no one is admitted" not in (OPS / "go-live.sh").read_text()


def test_start_prints_the_ops_identity_for_the_origin_commit(clone):
    _, work = clone
    out = _probe(work)
    assert out.returncode == 0, out.stdout + out.stderr
    assert f"ops {_git(work, 'rev-parse', '--short=8', 'HEAD')}" in out.stdout and "STARTED" in out.stdout


def test_start_pulls_new_commits_by_itself(clone):
    seed, work = clone
    (seed / "x.txt").write_text("new")
    _git(seed, "add", "-A"); _git(seed, "commit", "-qm", "more"); _git(seed, "push", "-q", "origin", "main")
    out = _probe(work)
    assert out.returncode == 0, out.stdout + out.stderr
    assert _git(work, "rev-parse", "HEAD") == _git(seed, "rev-parse", "HEAD")


def test_start_aborts_when_an_ops_file_was_edited(clone):
    _, work = clone
    (work / "scripts" / "ops" / "lib.sh").write_text((work / "scripts" / "ops" / "lib.sh").read_text() + "\n# edited\n")
    out = _probe(work)
    assert out.returncode == 1 and "ABORTED [DIRTY]" in out.stdout and "Fix:" in out.stdout and "Then run: scripts/ops/probe.sh" in out.stdout
    assert "STARTED" not in out.stdout


def test_start_aborts_on_a_branch_with_own_work_and_changes_nothing(clone):
    _, work = clone
    _git(work, "checkout", "-q", "-b", "mine")
    (work / "mine.txt").write_text("x"); _git(work, "add", "-A"); _git(work, "commit", "-qm", "mine")
    out = _probe(work)
    assert "ABORTED [BRANCH]" in out.stdout
    assert _git(work, "rev-parse", "--abbrev-ref", "HEAD") == "mine"


def test_start_aborts_when_origin_is_another_repository(clone):
    _, work = clone
    _git(work, "remote", "set-url", "origin", str(work.parent / "ShyAlon" / "delivery-api.git").replace("delivery-api", "other"))
    out = _probe(work)
    assert "ABORTED [REMOTE]" in out.stdout

