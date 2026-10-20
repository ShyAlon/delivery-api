"""The server image build: independent of the caller's umask, tagged only after the smoke check passes, and never reused on trust.

Hermetic: the real build, smoke and deploy-full scripts run against a fake ssh; nothing touches a server or Docker.
"""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git")

FAKE_SSH = r'''#!/usr/bin/env bash
c="${@: -1}"
echo "ssh $c" >> "$FAKE_LOG"
case "$c" in
  *full-smoke.py*) exit "${FAKE_SMOKE_RC:-0}";;
  *"docker rmi"*) exit "${FAKE_RMI_RC:-0}";;
  *"tar -x"*) mkdir -p "$FAKE_UP"; tar -x -p -C "$FAKE_UP";;
  *MODE=list*) cat > /dev/null; echo "stale="; echo "caddy=no-file";;
  *docker=ok*) echo docker=ok; echo "has control-plane.env"; echo "image=${FAKE_IMAGE_STATE:-present}"; echo running=none;;
  *"docker info"*) echo ok;;
esac
exit 0
'''


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}).stdout.strip()


def _repo(tmp_path, name, files):
    bare = tmp_path / "ShyAlon" / (name + ".git")
    bare.parent.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    seed = tmp_path / ("seed-" + name)
    _git(tmp_path, "clone", "-q", str(bare), str(seed))
    for rel, body in files.items():
        f = seed / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, Path):
            shutil.copy(body, f)
        else:
            f.write_text(body)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "x")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-q", "origin", "main")
    (tmp_path / "repos").mkdir(exist_ok=True)
    _git(tmp_path, "clone", "-q", str(bare), str(tmp_path / "repos" / name))
    return tmp_path / "repos" / name


def _setup(tmp_path, real_build=True):
    docker = ROOT / "scripts" / "docker"
    ops = ROOT / "scripts" / "ops"
    api_files = {
        "Dockerfile.full": "FROM scratch\n", "scripts/docker/smoke-image.sh": docker / "smoke-image.sh",
        "scripts/docker/full-smoke.py": "print('x')\n", "scripts/docker/full_entry.py": "print('y')\n",
        "scripts/ops/lib.sh": ops / "lib.sh", "scripts/ops/deploy-full.sh": ops / "deploy-full.sh",
        "scripts/docker/build-full-image.sh": docker / "build-full-image.sh" if real_build else 'echo BUILD-RAN >> "$FAKE_LOG"\n',
    }
    api = _repo(tmp_path, "delivery-api", api_files)
    for n in ("interview-engine", "spec-generator", "verification-loop"):
        _repo(tmp_path, n, {"README": n, "pkg/mod.py": "x = 1\n"})
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "ssh").write_text(FAKE_SSH)
    (fake / "ssh").chmod(0o755)
    yes = tmp_path / "yes"
    yes.write_text("yes\n")
    log = tmp_path / "log"
    log.write_text("")
    env = {**os.environ, "PATH": "%s:%s" % (fake, os.environ["PATH"]), "REQUI_REPOS_ROOT": str(tmp_path / "repos"), "FAKE_LOG": str(log),
           "FAKE_UP": str(tmp_path / "uploaded"), "BUILD_TTY": str(yes)}
    return api, env, log, tmp_path


def _build(api, env, umask="077", **extra):
    return subprocess.run(["bash", "-c", "umask %s; bash scripts/docker/build-full-image.sh" % umask], cwd=api, capture_output=True, text=True, env={**env, **extra}, timeout=60)


def test_the_uploaded_build_context_is_readable_whatever_the_callers_umask(tmp_path):
    api, env, log, _ = _setup(tmp_path)
    out = _build(api, env, umask="077")
    assert out.returncode == 0, out.stdout + out.stderr
    up = tmp_path / "uploaded"
    files = [p for p in up.rglob("*") if p.is_file()]
    assert any(p.name == "full-smoke.py" for p in files) and any(p.name == "mod.py" for p in files)
    for p in files:
        assert stat.S_IMODE(p.stat().st_mode) & 0o044 == 0o044, p
    for p in [x for x in up.rglob("*") if x.is_dir()]:
        assert stat.S_IMODE(p.stat().st_mode) & 0o055 == 0o055, p


def test_the_final_tag_is_published_only_after_the_smoke_passes(tmp_path):
    api, env, log, _ = _setup(tmp_path)
    assert _build(api, env).returncode == 0
    lines = log.read_text().splitlines()
    build = [i for i, l in enumerate(lines) if "docker build" in l][0]
    smoke = [i for i, l in enumerate(lines) if "full-smoke.py" in l][0]
    tag = [i for i, l in enumerate(lines) if "docker tag requi-full-candidate:" in l][0]
    assert build < smoke < tag
    assert " -t requi-full-candidate:" in lines[build] and " -t requi-full:" not in lines[build]
    assert "requi-full-candidate:" in lines[smoke] and "docker rmi requi-full-candidate:" in lines[tag]


def test_a_failed_smoke_creates_no_final_tag_and_removes_only_the_candidate(tmp_path):
    api, env, log, _ = _setup(tmp_path)
    out = _build(api, env, FAKE_SMOKE_RC="1")
    assert out.returncode != 0 and "ABORTED [SMOKE]" in out.stdout and "Fix:" in out.stdout and "Then run:" in out.stdout
    text = log.read_text()
    assert "docker tag" not in text
    rmi = [l for l in text.splitlines() if "docker rmi" in l]
    assert len(rmi) == 1 and "requi-full-candidate:" in rmi[0]
    assert "was removed from the server" in out.stdout and "was not created" in out.stdout


def test_a_failed_smoke_that_cannot_be_removed_says_so(tmp_path):
    api, env, log, _ = _setup(tmp_path)
    out = _build(api, env, FAKE_SMOKE_RC="1", FAKE_RMI_RC="1")
    assert "ABORTED [SMOKE]" in out.stdout and "could NOT be removed" in out.stdout and "docker tag" not in log.read_text()


def test_the_smoke_check_only_touches_exact_requi_full_tags(tmp_path):
    api, env, log, _ = _setup(tmp_path)
    for bad in ("ubuntu:latest", "requi-full:x;rm -rf /", "requi-full:", ""):
        out = subprocess.run(["bash", "scripts/docker/smoke-image.sh", bad], cwd=api, capture_output=True, text=True, env=env, timeout=30)
        assert out.returncode == 2 and "refusing" in out.stdout
    assert log.read_text() == ""


def _deploy(api, env, **extra):
    return subprocess.run(["bash", "scripts/ops/deploy-full.sh"], cwd=api, input="no\n", capture_output=True, text=True, env={**env, **extra}, timeout=90)


def test_deploy_full_smoke_checks_an_existing_image_and_does_not_rebuild_when_it_passes(tmp_path):
    api, env, log, _ = _setup(tmp_path, real_build=False)
    out = _deploy(api, env)
    text = log.read_text()
    assert "full-smoke.py" in text and "BUILD-RAN" not in text and "DECLINED" in out.stdout, out.stdout + out.stderr


def test_deploy_full_removes_a_failed_existing_image_by_exact_tag_and_builds_again(tmp_path):
    api, env, log, _ = _setup(tmp_path, real_build=False)
    out = _deploy(api, env, FAKE_SMOKE_RC="1")
    text = log.read_text()
    rmi = [l for l in text.splitlines() if "docker rmi" in l]
    assert len(rmi) == 1 and "requi-full:" in rmi[0] and text.count("BUILD-RAN") == 1, out.stdout + out.stderr


def test_deploy_full_stops_before_the_service_when_a_failed_image_cannot_be_removed(tmp_path):
    api, env, log, _ = _setup(tmp_path, real_build=False)
    out = _deploy(api, env, FAKE_SMOKE_RC="1", FAKE_RMI_RC="1")
    assert "ABORTED [IMAGE-SMOKE]" in out.stdout and "Fix:" in out.stdout and "BUILD-RAN" not in log.read_text()
    assert "PLAN" not in out.stdout and "docker=ok" not in log.read_text().split("full-smoke.py")[-1]
