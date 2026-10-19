"""The req runner: runs a stamped script without an age prompt, still refuses expired or edited ones."""
import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REQ = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "req"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("shasum") is None, reason="needs bash and shasum")


def _script(directory, name, body="echo RAN-IT\n", expires="2999-01-01T00:00:00Z", hours_old=3):
    text = "#!/usr/bin/env bash\n" + body
    sha = hashlib.sha256(text.encode()).hexdigest()[:8]
    path = Path(directory) / f"{name}.txt"
    path.write_text(f"#!/usr/bin/env bash\n# REQUI-SCRIPT {name} {sha} expires={expires}  (run it with:  req)\n{body}")
    old = time.time() - hours_old * 3600
    os.utime(path, (old, old))
    return path


def _req(directory, *args):
    return subprocess.run(["bash", str(REQ), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60,
                          env={**os.environ, "REQ_DOWNLOADS": str(directory)})


def test_the_age_prompt_is_gone_from_the_runner():
    assert "outdated" not in REQ.read_text() and "minutes ago" not in REQ.read_text()


def test_a_script_downloaded_hours_ago_runs_with_no_question(tmp_path):
    script = _script(tmp_path, "probe", hours_old=5)
    out = _req(tmp_path)
    assert out.returncode == 0 and "RAN-IT" in out.stdout, out.stdout + out.stderr
    assert not script.exists() and list((tmp_path / "requi-done").glob("probe-*.txt"))


def test_an_expired_script_is_archived_and_not_run(tmp_path):
    _script(tmp_path, "old", expires="2001-01-01T00:00:00Z")
    out = _req(tmp_path)
    assert "RAN-IT" not in out.stdout and "expired and archived" in out.stdout and out.returncode == 1
    assert list((tmp_path / "requi-done" / "expired").glob("old-*.txt"))


def test_an_edited_script_aborts_and_runs_nothing(tmp_path):
    path = _script(tmp_path, "edited")
    path.write_text(path.read_text() + "echo EXTRA\n")
    out = _req(tmp_path)
    assert out.returncode == 1 and "damaged or edited" in out.stdout and "RAN-IT" not in out.stdout and "EXTRA" not in out.stdout
    assert path.exists()


def test_a_failing_script_stays_so_req_can_be_run_again(tmp_path):
    path = _script(tmp_path, "bad", body="echo ran; exit 3\n")
    out = _req(tmp_path)
    assert out.returncode == 3 and path.exists()


def test_clean_keeps_the_newest_and_archives_the_other_pending_scripts(tmp_path):
    old = _script(tmp_path, "stale-one", hours_old=9)
    mid = _script(tmp_path, "stale-two", hours_old=6)
    new = _script(tmp_path, "fresh", hours_old=1)
    out = _req(tmp_path, "clean")
    assert out.returncode == 0 and "kept (newest): fresh" in out.stdout and "RAN-IT" not in out.stdout, out.stdout + out.stderr
    assert new.exists() and not old.exists() and not mid.exists()
    assert len(list((tmp_path / "requi-done" / "stale").glob("stale-*.txt"))) == 2


def test_clean_with_nothing_pending_says_so(tmp_path):
    out = _req(tmp_path, "clean")
    assert out.returncode == 0 and "no pending Requi.re script" in out.stdout
