"""ops preview-provision: the one-time step that saves the live preview settings on this Mac only.

Hermetic: throwaway git repository, answers fed from a file. Nothing touches a server.
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1] / "scripts" / "ops"
SECRET = "postgresql://user:SECRETPASS@db.example:5432/x"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git")


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}).stdout.strip()


@pytest.fixture()
def env(tmp_path):
    bare = tmp_path / "ShyAlon" / "delivery-api.git"
    bare.parent.mkdir(parents=True)
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(bare), str(seed))
    (seed / "scripts" / "ops").mkdir(parents=True)
    shutil.copy(OPS / "lib.sh", seed / "scripts" / "ops" / "lib.sh")
    shutil.copy(OPS / "settings.py", seed / "scripts" / "ops" / "settings.py")
    shutil.copy(OPS / "preview-provision.sh", seed / "scripts" / "ops" / "preview-provision.sh")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "x")
    _git(seed, "push", "-q", "origin", "main")
    api = tmp_path / "repos" / "delivery-api"
    _git(tmp_path, "clone", "-q", str(bare), str(api))
    (api / '.venv').symlink_to(Path(sys.executable).resolve().parent.parent, target_is_directory=True)
    store = tmp_path / "cfg" / "requi" / "preview.env"
    answers = tmp_path / "answers"
    e = {**os.environ, "PREVIEW_SETTINGS_FILE": str(store), "PREVIEW_TTY": str(answers), "TMPDIR": str(tmp_path)}
    return api, e, store, answers


def _run(api, e, answers, lines):
    Path(answers).write_text("\n".join(lines) + "\n")
    return subprocess.run(["bash", "scripts/ops/preview-provision.sh"], cwd=api, capture_output=True, text=True, env=e, timeout=60)


GOOD = ["yes", SECRET, "https://s.example", "anon-key-123", "sub-1,sub-2", "admin-token-9"]


def test_saves_mode_600_with_exact_contents_and_never_prints_the_values(env):
    api, e, store, answers = env
    out = _run(api, e, answers, GOOD)
    assert out.returncode == 0, out.stdout + out.stderr
    assert oct(store.stat().st_mode & 0o777) == "0o600"
    text = store.read_text()
    for line in ("DELIVERY_DATABASE_URL=%s" % SECRET, "DELIVERY_SUPABASE_URL=https://s.example",
                 "DELIVERY_SUPABASE_ANON_KEY=anon-key-123", "DELIVERY_SUPABASE_ADMIN_SUBJECTS=sub-1,sub-2",
                 "DELIVERY_ADMIN_TOKEN=admin-token-9"):
        assert line in text
    assert "SECRETPASS" not in out.stdout + out.stderr and "anon-key-123" not in out.stdout and "admin-token-9" not in out.stdout
    assert out.stdout.count("saved") >= 5 and "chars" not in out.stdout and "mode 600" in out.stdout


def test_a_bad_database_address_is_rejected_and_asked_again(env):
    api, e, store, answers = env
    out = _run(api, e, answers, ["yes", "sqlite:///x.db", *GOOD[1:]])
    assert out.returncode == 0, out.stdout + out.stderr
    assert "must start with postgres://" in out.stdout
    assert "DELIVERY_DATABASE_URL=%s" % SECRET in store.read_text()


def test_a_bad_supabase_address_is_rejected_and_asked_again(env):
    api, e, store, answers = env
    out = _run(api, e, answers, ["yes", SECRET, "http://insecure.example", *GOOD[2:]])
    assert out.returncode == 0, out.stdout + out.stderr
    assert "must start with https://" in out.stdout


def test_an_empty_required_value_is_rejected_and_asked_again(env):
    api, e, store, answers = env
    out = _run(api, e, answers, ["yes", "", *GOOD[1:]])
    assert out.returncode == 0, out.stdout + out.stderr
    assert "cannot be empty" in out.stdout and "DELIVERY_DATABASE_URL=%s" % SECRET in store.read_text()


def test_the_optional_admin_token_can_be_skipped(env):
    api, e, store, answers = env
    out = _run(api, e, answers, [*GOOD[:-1], ""])
    assert out.returncode == 0, out.stdout + out.stderr
    assert "DELIVERY_ADMIN_TOKEN" not in store.read_text() and "skipped" in out.stdout


def test_without_a_typed_yes_nothing_is_written(env):
    api, e, store, answers = env
    out = _run(api, e, answers, ["no", *GOOD[1:]])
    assert "ABORTED [NOT-CONFIRMED]" in out.stdout and not store.exists()


def test_an_existing_file_is_kept_unless_yes_is_typed(env):
    api, e, store, answers = env
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("DELIVERY_DATABASE_URL=postgresql://old\n")
    store.chmod(0o600)
    out = _run(api, e, answers, ["no"])
    assert "ABORTED [NOT-CONFIRMED]" in out.stdout and store.read_text() == "DELIVERY_DATABASE_URL=postgresql://old\n"
    assert "REPLACES" in out.stdout
    out = _run(api, e, answers, GOOD)
    assert out.returncode == 0 and "DELIVERY_DATABASE_URL=%s" % SECRET in store.read_text()
    assert oct(store.stat().st_mode & 0o777) == "0o600"
