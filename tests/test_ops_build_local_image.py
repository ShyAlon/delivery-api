"""scripts/docker/build-local-image.sh: build the full image on this Mac, smoke it, publish the tag only on success.

Hermetic: fake docker on PATH; throwaway git repositories. Nothing touches a server or Docker.
"""
import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NATIVE = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}[platform.machine().lower()]
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git")

FAKE_DOCKER = r'''#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
case "$1" in
  info) :;;
  buildx) :;;
  build) [ -z "${FAKE_BUILD_RC:-}" ] || exit "$FAKE_BUILD_RC";;
  run) case "$*" in *full-smoke.py*) exit "${FAKE_SMOKE_RC:-0}";; esac;;
  tag) :;;
  image) exit 1;;
  rmi) exit "${FAKE_RMI_RC:-0}";;
esac
'''


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}).stdout.strip()


@pytest.fixture()
def env(tmp_path):
    root = tmp_path / "repos"
    for name in ("delivery-api", "interview-engine", "spec-generator", "verification-loop"):
        bare = tmp_path / "ShyAlon" / (name + ".git")
        bare.parent.mkdir(parents=True, exist_ok=True)
        _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
        seed = tmp_path / ("seed-" + name)
        _git(tmp_path, "clone", "-q", str(bare), str(seed))
        if name == "delivery-api":
            (seed / "scripts" / "docker").mkdir(parents=True)
            shutil.copy(REPO / "scripts" / "docker" / "build-local-image.sh", seed / "scripts" / "docker" / "build-local-image.sh")
            (seed / "Dockerfile.full").write_text("FROM scratch\n")
        else:
            (seed / "README").write_text(name)
        _git(seed, "add", "-A")
        _git(seed, "commit", "-qm", "x")
        _git(seed, "branch", "-M", "main")
        _git(seed, "push", "-q", "origin", "main")
        _git(tmp_path, "clone", "-q", str(bare), str(root / name))
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "docker").write_text(FAKE_DOCKER)
    (fake / "docker").chmod(0o755)
    (fake / "uname").write_text("#!/usr/bin/env bash\nif [ -n \"${FAKE_UNAME_M:-}\" ]; then echo \"$FAKE_UNAME_M\"; else /usr/bin/uname \"$@\"; fi\n")
    (fake / "uname").chmod(0o755)
    yes = tmp_path / "yes"
    yes.write_text("yes\n")
    log = tmp_path / "log"
    log.write_text("")
    e = {**os.environ, "PATH": "%s:%s" % (fake, os.environ["PATH"]), "REQUI_REPOS_ROOT": str(root),
         "FAKE_LOG": str(log), "BUILD_TTY": str(yes), "TMPDIR": str(tmp_path)}
    return root / "delivery-api", e, log, tmp_path


def _run(api, e, **extra):
    return subprocess.run(["bash", "scripts/docker/build-local-image.sh"], cwd=api, capture_output=True, text=True, env={**e, **extra}, timeout=60)


def test_a_clean_set_builds_smokes_and_publishes_the_tag(env):
    api, e, log, _ = env
    out = _run(api, e)
    assert out.returncode == 0, out.stdout + out.stderr
    text = log.read_text()
    build = [l for l in text.splitlines() if l.startswith("docker build")][0]
    assert "--platform linux/%s" % NATIVE in build and "REQUI_BUILD_REPOS=" in build
    for name in ("delivery-api", "interview-engine", "spec-generator", "verification-loop"):
        assert name in build
    lines = text.splitlines()
    assert lines.index(build) < [i for i, l in enumerate(lines) if "full-smoke.py" in l][0] < [i for i, l in enumerate(lines) if l.startswith("docker tag")][0]
    assert "== built requi-full:" in out.stdout and "local build, no server contact" in out.stdout


def test_a_failed_smoke_removes_the_candidate_and_publishes_nothing(env):
    api, e, log, _ = env
    out = _run(api, e, FAKE_SMOKE_RC="1")
    assert out.returncode != 0 and "ABORTED [SMOKE]" in out.stdout and "removed the failed image tag" in out.stdout
    text = log.read_text()
    assert "docker rmi requi-full-candidate:" in text and "docker tag" not in text


def test_a_candidate_that_cannot_be_removed_is_not_approved(env):
    api, e, log, _ = env
    out = _run(api, e, FAKE_SMOKE_RC="1", FAKE_RMI_RC="1")
    assert "ABORTED [SMOKE]" in out.stdout and "NOT approved" in out.stdout and "docker tag" not in log.read_text()


def test_a_dirty_checkout_builds_nothing(env):
    api, e, log, tmp = env
    (tmp / "repos" / "interview-engine" / "README").write_text("dirty")
    out = _run(api, e)
    assert "ABORTED [DIRTY-interview-engine]" in out.stdout and "docker build" not in log.read_text()


def test_without_a_typed_yes_nothing_is_built(env, tmp_path):
    api, e, log, _ = env
    (tmp_path / "yes").write_text("no\n")
    out = _run(api, e)
    assert "ABORTED [NOT-CONFIRMED]" in out.stdout and "docker build" not in log.read_text()


def test_an_unknown_architecture_value_is_refused(env):
    api, e, log, _ = env
    out = _run(api, e, PREVIEW_ARCH="sparc")
    assert "ABORTED [ARCH]" in out.stdout and "docker build" not in log.read_text()
