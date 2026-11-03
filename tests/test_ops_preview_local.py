"""ops preview, default local flow: the image is built on this Mac, settings come from the file saved by
ops preview-provision, and no server contact happens at all.

Hermetic: fake docker, curl and ssh on PATH; throwaway git repositories. Nothing touches a server or Docker.
"""
import os
import sys
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1] / "scripts" / "ops"
SECRET = "postgresql://user:SECRETPASS@db.example:5432/x"
NATIVE = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}[platform.machine().lower()]
OTHER_ARCH = "arm64" if NATIVE == "amd64" else "amd64"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git")

FAKE_SSH = '#!/usr/bin/env bash\necho "ssh $*" >> "$FAKE_LOG"\nexit 1\n'
FAKE_DOCKER = r'''#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
case "$1" in
  info) [ -z "${NO_DOCKER:-}" ] || exit 1;;
  build) [ -z "${FAKE_BUILD_RC:-}" ] || exit "$FAKE_BUILD_RC"; touch "$FAKE_STATE/built";;
  tag) :;;
  ps) if [ "${2:-}" = "-a" ]; then [ -z "${LEFTOVER:-}" ] || echo requi-preview; else echo requi-preview; fi;;
  image) case "$*" in
           *Architecture*) if [ -n "${FAKE_ARCH:-}" ]; then echo "$FAKE_ARCH"; else uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/'; fi;;
           *) [ -f "$FAKE_STATE/built" ] && exit 0; [ -z "${FAKE_NO_IMAGE:-}" ] || exit 1;; esac;;
  rmi) exit "${FAKE_RMI_RC:-0}";;
  load) cat > /dev/null;;
  run) for a in "$@"; do
         if [ "${prev:-}" = "--env-file" ]; then stat -c %a "$a" 2>/dev/null >> "$FAKE_LOG" || stat -f %Lp "$a" >> "$FAKE_LOG"; cp "$a" "$FAKE_LOG.env"; echo "$a" > "$FAKE_LOG.envpath"; fi; prev=$a; done
       case "$*" in *check_env*) printf '%b\n' "${CONTRACT_OUT:-CONTRACT-OK}";; *full-smoke.py*) exit "${FAKE_SMOKE_RC:-0}";; *"python -c"*) echo DBOK;; esac;;
  logs) case "${LOGS_MODE:-closed}" in closed) echo "Cloud execution: CLOSED - x";; open) echo "Cloud execution: OPEN (x)";; absent) echo "Server log: /x";; esac;;
  exec) printf '%b\n' "${EXEC_OUT:-EXEC=0\nKEYS=0}";;
  rm) :;;
esac
'''


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}).stdout.strip()


def _repo(tmp_path, name, with_ops):
    bare = tmp_path / "ShyAlon" / (name + ".git")
    bare.parent.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    seed = tmp_path / ("seed-" + name)
    _git(tmp_path, "clone", "-q", str(bare), str(seed))
    if with_ops:
        (seed / "scripts" / "ops").mkdir(parents=True)
        (seed / "scripts" / "docker").mkdir(parents=True)
        shutil.copy(OPS / "lib.sh", seed / "scripts" / "ops" / "lib.sh")
        shutil.copy(OPS / "terminal-log.sh", seed / "scripts" / "ops" / "terminal-log.sh")
        shutil.copy(OPS / "settings.py", seed / "scripts" / "ops" / "settings.py")
        shutil.copy(OPS / "preview.sh", seed / "scripts" / "ops" / "preview.sh")
        shutil.copy(OPS.parent / "docker" / "build-local-image.sh", seed / "scripts" / "docker" / "build-local-image.sh")
        for relative in ('scripts/docker/smoke-local-image.sh','scripts/docker/smoke-context.py','scripts/docker/Dockerfile.pg-smoke','scripts/docker/owned-pg-smoke.py','scripts/docker/system-pg-smoke.py','tests/pg_harness.py','tests/pg_guardian.py','src/delivery_api/runtime_roles.py','supabase/migrations/20261030000000_test.sql'):
            target=seed/relative;target.parent.mkdir(parents=True,exist_ok=True)
            source=Path(__file__).resolve().parents[1]/relative
            target.write_text(source.read_text() if source.exists() else '-- fixture migration\n')
        (seed / "scripts" / "docker" / "build-full-image.sh").write_text("echo SERVER-BUILD-SHOULD-NOT-RUN >> \"$FAKE_LOG\"\n")
        (seed / "scripts" / "docker" / "smoke-image.sh").write_text("echo SERVER-SMOKE-SHOULD-NOT-RUN >> \"$FAKE_LOG\"\nexit 9\n")
        (seed / "Dockerfile.full").write_text("FROM scratch\n")
    else:
        (seed / "README").write_text(name)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "x")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-q", "origin", "main")
    root = tmp_path / "repos"
    root.mkdir(exist_ok=True)
    _git(tmp_path, "clone", "-q", str(bare), str(root / name))
    if with_ops:
        (root / name / '.venv').symlink_to(Path(sys.prefix), target_is_directory=True)
    return root / name


@pytest.fixture()
def env(tmp_path):
    root = tmp_path / "repos"
    api = _repo(tmp_path, "delivery-api", True)
    for n in ("interview-engine", "spec-generator", "verification-loop"):
        _repo(tmp_path, n, False)
    fake = tmp_path / "bin"
    fake.mkdir()
    for n, body in (("ssh", FAKE_SSH), ("docker", FAKE_DOCKER), ("curl", "#!/usr/bin/env bash\n[ -z \"${CURL_FAIL:-}\" ]\n"),
                    ("uname", "#!/usr/bin/env bash\nif [ -n \"${FAKE_UNAME_M:-}\" ]; then echo \"$FAKE_UNAME_M\"; else /usr/bin/uname \"$@\"; fi\n")):
        (fake / n).write_text(body)
        (fake / n).chmod(0o755)
    store = tmp_path / "store.env"
    store.write_text("DELIVERY_DATABASE_URL=%s\nDELIVERY_SUPABASE_URL=https://s.example\nDELIVERY_SUPABASE_ANON_KEY=anon\nDELIVERY_SUPABASE_ADMIN_SUBJECTS=sub-1\nDELIVERY_ADMIN_TOKEN=tok\nDELIVERY_CLOUD_EXECUTION=1\nDEEPSEEK_API_KEY=sk-model\nDELIVERY_LOCAL_PREVIEW=0\n" % SECRET)
    store.chmod(0o600)
    yes = tmp_path / "yes"
    yes.write_text("yes\n")
    log = tmp_path / "log"
    log.write_text("")
    e = {**{k:v for k,v in os.environ.items() if not k.startswith(("DELIVERY_","RESET_","DEEPSEEK_","PREVIEW_","REQUI_SETTINGS_"))}, "PATH": "%s:%s" % (fake, os.environ["PATH"]), "REQUI_REPOS_ROOT": str(root), "PREVIEW_SETTINGS_FILE": str(store),
         "FAKE_LOG": str(log), "FAKE_STATE": str(tmp_path), "PREVIEW_TTY": str(yes), "BUILD_TTY": str(yes),
         "PREVIEW_PORT": "18766", "PREVIEW_WAIT_TRIES": "2", "PREVIEW_POLL": "0", "TMPDIR": str(tmp_path)}
    return api, e, log, tmp_path


def _run(api, e, **extra):
    return subprocess.run(["bash", "scripts/ops/preview.sh"], cwd=api, capture_output=True, text=True, env={**e, **extra}, timeout=120)


def test_the_local_preview_runs_with_no_server_contact_at_all(env):
    api, e, log, _ = env
    out = _run(api, e)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "PREVIEW READY: http://127.0.0.1:18766" in out.stdout
    assert "no server access" in out.stdout and "Not part of a local preview" in out.stdout
    text = log.read_text()
    assert "ssh" not in text and "SERVER-BUILD-SHOULD-NOT-RUN" not in text and "SERVER-SMOKE-SHOULD-NOT-RUN" not in text
    assert "full-smoke.py" in text  # the reused image was smoke checked locally first
    assert "SECRETPASS" not in out.stdout + out.stderr and "sk-model" not in out.stdout + out.stderr


def test_the_forced_settings_carry_the_local_preview_flag_and_execution_off(env):
    api, e, log, _ = env
    assert _run(api, e).returncode == 0
    settings = Path(str(log) + ".env").read_text().splitlines()
    assert settings.count("DELIVERY_LOCAL_PREVIEW=1") == 1 and not any(l.startswith("DELIVERY_LOCAL_PREVIEW=0") for l in settings)
    assert settings.count("DELIVERY_CLOUD_EXECUTION=0") == 1 and not any(l.startswith("DELIVERY_CLOUD_EXECUTION=1") for l in settings)
    assert "DELIVERY_PREVIEW=1" in settings and "DELIVERY_DATABASE_URL=%s" % SECRET in settings
    assert "DELIVERY_SUPABASE_ADMIN_SUBJECTS=sub-1" in settings and "DELIVERY_ADMIN_TOKEN=tok" in settings
    assert not any("DEEPSEEK" in l or "API_KEY" in l.upper() for l in settings)


def test_a_missing_image_is_built_locally_on_this_macs_architecture(env):
    api, e, log, _ = env
    out = _run(api, e, FAKE_NO_IMAGE="1")
    assert out.returncode == 0, out.stdout + out.stderr
    text = log.read_text()
    build = [l for l in text.splitlines() if l.startswith("docker build")][0]
    assert "--platform linux/%s" % NATIVE in build and "REQUI_BUILD_REPOS=" in build
    assert "docker tag requi-full-candidate:" in text and "docker load" not in text
    assert "ssh" not in text


def test_an_image_of_the_wrong_architecture_is_refused(env):
    api, e, log, _ = env
    out = _run(api, e, FAKE_ARCH=OTHER_ARCH)
    assert "ABORTED [ARCH]" in out.stdout and OTHER_ARCH in out.stdout and "docker run -d" not in log.read_text()


def test_without_saved_settings_it_points_to_provisioning_and_starts_nothing(env, tmp_path):
    api, e, log, _ = env
    out = _run(api, e, PREVIEW_SETTINGS_FILE=str(tmp_path / "absent.env"))
    assert "ABORTED [SETTINGS]" in out.stdout and "preview-provision.sh" in out.stdout
    assert "docker run" not in log.read_text()


def test_saved_settings_must_be_mode_600(env):
    api, e, log, _ = env
    Path(e["PREVIEW_SETTINGS_FILE"]).chmod(0o644)
    out = _run(api, e)
    assert "ABORTED [SETTINGS]" in out.stdout and "docker run" not in log.read_text()


def test_a_server_side_session_check_is_refused_in_the_local_flow(env):
    api, e, log, _ = env
    out = _run(api, e, PREVIEW_CHECK_SESSION="abc123")
    assert "ABORTED [LOCAL-NO-CHK]" in out.stdout and "PREVIEW_VIA_SERVER=1" in out.stdout
    assert "ssh" not in log.read_text() and "docker run" not in log.read_text()


def test_without_a_typed_yes_nothing_is_started(env, tmp_path):
    api, e, log, _ = env
    (tmp_path / "yes").write_text("no\n")
    out = _run(api, e)
    assert "ABORTED [NOT-CONFIRMED]" in out.stdout and "docker run" not in log.read_text()


def test_the_real_database_warning_is_shown_before_the_yes(env):
    api, e, log, _ = env
    out = _run(api, e)
    assert "REAL one" in out.stdout and "Grant Credits" in out.stdout and 'Type "yes"' in out.stdout


def test_readiness_needs_a_positive_closed_report_and_fails_closed(env):
    api, e, log, _ = env
    for extra, code in (({"LOGS_MODE": "open"}, "EXEC-UNKNOWN"), ({"LOGS_MODE": "absent"}, "EXEC-UNKNOWN"),
                        ({"EXEC_OUT": "EXEC=1\\nKEYS=0"}, "EXEC-UNVERIFIED"),
                        ({"CURL_FAIL": "1"}, "NOT-HEALTHY")):
        out = _run(api, e, **extra)
        assert out.returncode != 0 and "ABORTED [%s]" % code in out.stdout and "PREVIEW READY" not in out.stdout, (extra, out.stdout)
        assert "Fix:" in out.stdout and "Then run:" in out.stdout
    assert "docker rm -f requi-preview" in log.read_text()


def test_a_failed_build_starts_nothing(env):
    api, e, log, _ = env
    out = _run(api, e, FAKE_NO_IMAGE="1", FAKE_BUILD_RC="1")
    assert "ABORTED [BUILD]" in out.stdout and "docker run -d" not in log.read_text()

def test_on_an_arm64_mac_the_build_and_run_use_linux_arm64(env):
    api, e, log, _ = env
    out = _run(api, e, FAKE_NO_IMAGE="1", FAKE_UNAME_M="arm64", FAKE_ARCH="arm64")
    assert out.returncode == 0, out.stdout + out.stderr
    text = log.read_text()
    build = [l for l in text.splitlines() if l.startswith("docker build")][0]
    run = [l for l in text.splitlines() if l.startswith("docker run -d")][0]
    assert "--platform linux/arm64" in build and "--platform linux/arm64" in run
    assert "ssh" not in text


def test_on_an_arm64_mac_an_amd64_image_is_refused(env):
    api, e, log, _ = env
    out = _run(api, e, FAKE_UNAME_M="arm64", FAKE_ARCH="amd64")
    assert "ABORTED [ARCH]" in out.stdout and "docker run -d" not in log.read_text()

