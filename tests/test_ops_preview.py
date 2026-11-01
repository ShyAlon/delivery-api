"""ops preview: the candidate image on a Mac, real database, execution off.

Hermetic: fake ssh, docker, curl and tar on PATH; throwaway git repositories. Nothing touches a server or Docker.
"""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1] / "scripts" / "ops"
WEB = Path(__file__).resolve().parents[1] / "src" / "delivery_api" / "web"
SECRET = "postgresql://user:SECRETPASS@db.example:5432/x"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git")

FAKE_SSH = r'''#!/usr/bin/env bash
c="${@: -1}"
echo "ssh $c" >> "$FAKE_LOG"
case "$c" in
  *"mktemp -d /tmp/requi-image-smoke"*) echo /tmp/requi-image-smoke.FIXTURE123;;
  *"tar -x -C"*) cat >/dev/null;;
  *"smoke-local-image.sh"*) exit "${FAKE_SMOKE_RC:-0}";;
  *full-smoke.py*) exit "${FAKE_SMOKE_RC:-0}";;
  *"docker rmi"*) exit "${FAKE_RMI_RC:-0}";;
  *"--format"*"image"*|*"image inspect --format"*) echo sha256:fake;;
  *"docker info"*) echo ok;;
  *"http-session.json"*) echo "${FAKE_SESSION_SERVER:-NO}";;
  *"du -sm"*) echo "${FAKE_SIZE:-5}";;
  *"image inspect"*) exit 0;;
  *"docker save"*) :;;
  *"full.env"*) cat "$FAKE_ENV";;
  *"tar -C"*) if [ -n "${FAKE_COPY_FILE:-}" ]; then d=$(mktemp -d); mkdir -p "$d/$(dirname "$FAKE_COPY_FILE")"; echo '{"private":"CONTENTS-MUST-NOT-PRINT"}' > "$d/$FAKE_COPY_FILE"; tar -C "$d" -cf - .; else tar -cf - -T /dev/null; fi;;
esac
'''
FAKE_DOCKER = r'''#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
case "$1" in
  info) [ -z "${NO_DOCKER:-}" ] || exit 1;;
  ps) if [ "${2:-}" = "-a" ]; then [ -z "${LEFTOVER:-}" ] || echo requi-preview; else echo requi-preview; fi;;
  image) case "$*" in *Architecture*) echo amd64;; *"{{.Id}}"*) echo sha256:fake;; esac;;
  rmi) :;;
  load) cat > /dev/null;;
  run) for a in "$@"; do
         if [ "${prev:-}" = "--env-file" ]; then stat -c %a "$a" 2>/dev/null >> "$FAKE_LOG" || stat -f %Lp "$a" >> "$FAKE_LOG"; cp "$a" "$FAKE_LOG.env"; echo "$a" > "$FAKE_LOG.envpath"; fi; prev=$a; done
       case "$*" in *check_env*) printf '%b\n' "${CONTRACT_OUT:-CONTRACT-OK}";; *"python -c"*) echo DBOK;; esac;;
  logs) case "${LOGS_MODE:-closed}" in closed) echo "Cloud execution: CLOSED - x";; open) echo "Cloud execution: OPEN (x)";; absent) echo "Server log: /x";; esac; echo "connecting ${FAKE_SECRET:-}";;
  exec) printf '%b\n' "${EXEC_OUT:-EXEC=0\nKEYS=0}";;
  rm) :;;
esac
'''


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}).stdout.strip()


def _repo(tmp_path, owner, name, with_ops):
    bare = tmp_path / owner / (name + ".git")
    bare.parent.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    seed = tmp_path / ("seed-" + name)
    _git(tmp_path, "clone", "-q", str(bare), str(seed))
    if with_ops:
        (seed / "scripts" / "ops").mkdir(parents=True)
        (seed / "scripts" / "docker").mkdir(parents=True)
        for n in ("lib.sh", "preview.sh"):
            shutil.copy(OPS / n, seed / "scripts" / "ops" / n)
        shutil.copy(OPS.parent / "docker" / "smoke-image.sh", seed / "scripts" / "docker" / "smoke-image.sh")
        for relative in ('scripts/docker/smoke-local-image.sh','scripts/docker/Dockerfile.pg-smoke','scripts/docker/owned-pg-smoke.py','scripts/docker/system-pg-smoke.py','tests/pg_harness.py','tests/pg_guardian.py','supabase/migrations/20261030000000_test.sql'):
            target=seed/relative;target.parent.mkdir(parents=True,exist_ok=True)
            source=Path(__file__).resolve().parents[1]/relative
            target.write_text(source.read_text() if source.exists() else '-- fixture migration\n')
        (seed / "scripts" / "docker" / "build-full-image.sh").write_text("echo BUILD-SHOULD-NOT-RUN >> \"$FAKE_LOG\"\n")
    else:
        (seed / "README").write_text(name)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "x")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-q", "origin", "main")
    root = tmp_path / "repos"
    root.mkdir(exist_ok=True)
    _git(tmp_path, "clone", "-q", str(bare), str(root / name))
    return root / name


@pytest.fixture()
def env(tmp_path):
    root = tmp_path / "repos"
    api = _repo(tmp_path, "ShyAlon", "delivery-api", True)
    for n in ("interview-engine", "spec-generator", "verification-loop"):
        _repo(tmp_path, "ShyAlon", n, False)
    fake = tmp_path / "bin"
    fake.mkdir()
    for n, body in (("ssh", FAKE_SSH), ("docker", FAKE_DOCKER), ("curl", "#!/usr/bin/env bash\n[ -z \"${CURL_FAIL:-}\" ]\n")):
        (fake / n).write_text(body)
        (fake / n).chmod(0o755)
    live = tmp_path / "live.env"
    live.write_text("DELIVERY_DATABASE_URL=%s\nDELIVERY_SUPABASE_URL=https://s.example\nDELIVERY_CLOUD_EXECUTION=1\nDELIVERY_COOKIE_SECURE=1\nDEEPSEEK_API_KEY=sk-model\nOTHER_API_KEY=zzz\nOPENAI_KEY=o\nANTHROPIC_TOKEN=a\nGEMINI_API_KEY=g\nSTRIPE_SECRET=s\nDELIVERY_DEEPSEEK_KEY=d\nDELIVERY_MODEL_API_KEY=m\nDELIVERY_SUPABASE_ANON_KEY=anon\n" % SECRET)
    yes = tmp_path / "yes"
    yes.write_text("yes\n")
    log = tmp_path / "log"
    log.write_text("")
    e = {**os.environ, "PATH": "%s:%s" % (fake, os.environ["PATH"]), "REQUI_REPOS_ROOT": str(root), "FAKE_ENV": str(live),
         "FAKE_LOG": str(log), "PREVIEW_TTY": str(yes), "PREVIEW_PORT": "18765", "PREVIEW_WAIT_TRIES": "2", "PREVIEW_POLL": "0", "TMPDIR": str(tmp_path)}
    return api, e, log, tmp_path


def _run(api, e, **extra):
    return subprocess.run(["bash", "scripts/ops/preview.sh"], cwd=api, capture_output=True, text=True, env={**e, "PREVIEW_VIA_SERVER": "1", **extra}, timeout=60)  # these tests cover the SSH server flow; the default local flow has its own file


def test_it_aborts_with_fix_when_docker_is_not_running(env):
    api, e, log, _ = env
    out = _run(api, e, NO_DOCKER="1")
    assert out.returncode != 0 and "ABORTED [NO-DOCKER]" in out.stdout and "Fix:" in out.stdout and "Then run:" in out.stdout


def test_it_aborts_when_docker_is_missing(env, tmp_path):
    api, e, log, _ = env
    bare_path = tmp_path / "nodocker"
    bare_path.mkdir()
    for n in ("bash", "git", "ssh", "curl", "shasum", "tar", "env", "dirname", "grep", "sed", "cat", "date", "mktemp", "rm", "cut", "tr", "head", "sort", "seq", "chmod", "stat", "cp", "ls", "uname", "wc", "basename", "awk", "sleep", "readlink", "pwd"):
        w = shutil.which(n, path=e["PATH"])
        if w:
            (bare_path / n).symlink_to(w)
    out = _run(api, {**e, "PATH": str(bare_path)})
    assert out.returncode != 0 and "ABORTED [TOOL-docker]" in out.stdout and "Fix:" in out.stdout


def test_without_a_typed_yes_nothing_is_started(env, tmp_path):
    api, e, log, _ = env
    (tmp_path / "yes").write_text("no\n")
    out = _run(api, e)
    assert "ABORTED [NOT-CONFIRMED]" in out.stdout
    assert "docker run" not in log.read_text() and "docker load" not in log.read_text()
    assert not Path(str(log) + ".env").exists()


def test_the_real_database_warning_is_shown_before_the_yes(env):
    api, e, log, _ = env
    out = _run(api, e)
    assert "REAL one" in out.stdout and "Grant Credits" in out.stdout and 'Type "yes"' in out.stdout


def test_execution_is_forced_off_and_no_model_key_reaches_the_container(env):
    api, e, log, _ = env
    out = _run(api, e)
    assert out.returncode == 0, out.stdout + out.stderr
    settings = Path(str(log) + ".env").read_text().splitlines()
    assert settings.count("DELIVERY_CLOUD_EXECUTION=0") == 1 and not any(l.startswith("DELIVERY_CLOUD_EXECUTION=1") for l in settings)
    assert "DELIVERY_COOKIE_SECURE=0" in settings and "DELIVERY_PREVIEW=1" in settings and "DELIVERY_COOKIE_SECURE=1" not in settings
    assert not any(w in l.upper() for l in settings for w in ("API_KEY", "DEEPSEEK", "OPENAI", "ANTHROPIC", "GEMINI", "STRIPE", "SECRETPASS_X"))
    assert all(l.startswith(("DELIVERY_", "INTERVIEW_ENGINE_TOKEN=", "REQUI_EXPECTED_REPOS=")) for l in settings) and "DELIVERY_SUPABASE_ANON_KEY=anon" in settings
    assert any(l.startswith("DELIVERY_DATABASE_URL=") for l in settings)


def test_settings_are_private_never_printed_and_removed_on_exit(env):
    api, e, log, _ = env
    out = _run(api, e, FAKE_SECRET=SECRET)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "SECRETPASS" not in out.stdout + out.stderr and "sk-model" not in out.stdout + out.stderr
    modes = [l for l in log.read_text().splitlines() if l.strip().isdigit()]
    assert modes and all(m.strip() == "600" for m in modes)
    assert not Path(Path(str(log) + ".envpath").read_text().strip()).exists()


def test_the_container_is_removed_on_exit_and_hardened(env):
    api, e, log, _ = env
    assert _run(api, e).returncode == 0
    text = log.read_text()
    run = [l for l in text.splitlines() if l.startswith("docker run -d")][0]
    for flag in ("--read-only", "--cap-drop ALL", "no-new-privileges", "--pids-limit", "--memory", "127.0.0.1:18765:8000", "--platform linux/amd64"):
        assert flag in run
    assert "docker rm -f requi-preview" in text and "BUILD-SHOULD-NOT-RUN" not in text


def test_a_busy_port_stops_it_before_anything_starts(env):
    import socket
    api, e, log, _ = env
    s = socket.socket()
    s.bind(("127.0.0.1", 18765))
    s.listen(1)
    try:
        out = _run(api, e)
    finally:
        s.close()
    assert "ABORTED [PORT-BUSY]" in out.stdout and "docker run" not in log.read_text()


def test_a_large_data_folder_aborts_before_copying(env):
    api, e, log, _ = env
    out = _run(api, e, FAKE_SIZE="999999")
    assert "ABORTED [DATA-BIG]" in out.stdout and "Fix:" in out.stdout and "docker run" not in log.read_text()


def test_a_leftover_container_is_not_replaced_silently(env):
    api, e, log, _ = env
    out = _run(api, e, LEFTOVER="1")
    assert "ABORTED [LEFTOVER]" in out.stdout and "docker run" not in log.read_text()


def test_the_temp_folder_is_gone_after_a_run(env):
    api, e, log, tmp = env
    before = set(p.name for p in tmp.iterdir())
    assert _run(api, e).returncode == 0
    assert not [p for p in tmp.iterdir() if p.name not in before and p.name.startswith("tmp")]


@pytest.mark.skipif(subprocess.run(["which", "node"], capture_output=True).returncode != 0, reason="needs node")
def test_the_banner_appears_only_when_the_server_says_preview():
    import json
    src = r"""
const {previewBannerText}=await import(%r);
const none=[previewBannerText({preview:false}),previewBannerText({}),previewBannerText(null)];
console.log(JSON.stringify({none,one:previewBannerText({preview:true})}));
""" % (WEB / "chrome.js").as_uri()
    r = subprocess.run(["node", "--input-type=module", "-e", src], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out == {"none": [None, None, None], "one": "Preview: real data, execution off - changes here can affect live data"}


def test_only_the_preview_container_reports_preview(monkeypatch):
    from fastapi.testclient import TestClient
    import delivery_api.app as module
    c = TestClient(module.app)
    monkeypatch.delenv("DELIVERY_PREVIEW", raising=False)
    assert "preview" not in c.get("/api/auth").json()
    monkeypatch.setenv("DELIVERY_PREVIEW", "1")
    assert c.get("/api/auth").json()["preview"] is True


def test_readiness_needs_a_positive_closed_report_and_fails_closed(env):
    api, e, log, _ = env
    for extra, code in (({"LOGS_MODE": "open"}, "EXEC-UNKNOWN"), ({"LOGS_MODE": "absent"}, "EXEC-UNKNOWN"),
                        ({"EXEC_OUT": "EXEC=1\\nKEYS=0"}, "EXEC-UNVERIFIED"), ({"EXEC_OUT": "EXEC=0\\nKEYS=1"}, "EXEC-UNVERIFIED"),
                        ({"EXEC_OUT": "garbage"}, "EXEC-UNVERIFIED"), ({"CURL_FAIL": "1"}, "NOT-HEALTHY")):
        out = _run(api, e, **extra)
        assert out.returncode != 0 and "ABORTED [%s]" % code in out.stdout and "PREVIEW READY" not in out.stdout, (extra, out.stdout)
        assert "Fix:" in out.stdout and "Then run:" in out.stdout
    assert "docker rm -f requi-preview" in log.read_text()


def test_the_settings_file_is_private_from_its_first_byte():
    text = (OPS / "preview.sh").read_text()
    assert text.index("umask 077; WORK=$(mktemp -d") < text.index('full.env" > "$WORK/live.env"')
    r = subprocess.run(["bash", "-c", 'umask 022; PREVIEW_FUNCTIONS_ONLY=1 . %s; printf "DELIVERY_A=1\nOPENAI_KEY=x\n" > /tmp/in.$$; build_env /tmp/in.$$ /tmp/out.$$; stat -c %%a /tmp/out.$$ 2>/dev/null || stat -f %%Lp /tmp/out.$$; cat /tmp/out.$$; rm -f /tmp/in.$$ /tmp/out.$$' % (OPS / "preview.sh")],
                       capture_output=True, text=True)
    lines = r.stdout.split()
    assert lines[0] == "600" and "DELIVERY_A=1" in lines and not any("OPENAI" in l for l in lines)



def test_an_existing_image_is_smoke_checked_before_reuse_and_not_rebuilt_when_it_passes(env):
    api, e, log, _ = env
    assert _run(api, e).returncode == 0
    text = log.read_text()
    assert "smoke-local-image.sh" in text and "BUILD-SHOULD-NOT-RUN" not in text


def test_an_existing_image_that_fails_the_smoke_is_removed_by_exact_tag_and_rebuilt_once(env):
    api, e, log, _ = env
    _run(api, e, FAKE_SMOKE_RC="1")
    text = log.read_text()
    rm = [l for l in text.splitlines() if "docker rmi" in l]
    assert len(rm) == 1 and "requi-full:" in rm[0] and rm[0].count("requi-full") == 1
    assert text.count("BUILD-SHOULD-NOT-RUN") == 1


def test_a_failed_image_that_cannot_be_removed_stops_the_preview(env):
    api, e, log, _ = env
    out = _run(api, e, FAKE_SMOKE_RC="1", FAKE_RMI_RC="1")
    assert "ABORTED [IMAGE-SMOKE]" in out.stdout and "Fix:" in out.stdout and "Then run:" in out.stdout
    assert "BUILD-SHOULD-NOT-RUN" not in log.read_text()
    assert not [l for l in log.read_text().splitlines() if l.startswith("docker run") or l.startswith("docker load")]


def test_the_build_runs_with_a_normal_umask_not_the_private_one():
    text = (OPS / "preview.sh").read_text()
    assert "( umask 022; bash scripts/docker/build-full-image.sh )" in text


def _settings(tmp_path, live_lines):
    live = tmp_path / "in.env"
    live.write_text("\n".join(live_lines) + "\n")
    out = tmp_path / "out.env"
    r = subprocess.run(["bash", "-c", 'PREVIEW_FUNCTIONS_ONLY=1 . "%s"; build_env "%s" "%s"' % (OPS / "preview.sh", live, out)], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == "", r.stdout + r.stderr
    return dict(l.split("=", 1) for l in out.read_text().splitlines())


def _load_entry():
    import importlib.util
    spec = importlib.util.spec_from_file_location("full_entry_real", OPS.parent / "docker" / "full_entry.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_generated_settings_pass_the_images_real_startup_rules(tmp_path):
    entry = _load_entry()
    live = ["DELIVERY_DATABASE_URL=%s" % SECRET, "DELIVERY_CONTROL_PLANE_ONLY=1", "INTERVIEW_ENGINE_TOKEN=live-production-token-0123456789abcdef"]
    settings = _settings(tmp_path, live)
    assert entry.check_env(settings) == []
    assert settings["DELIVERY_CONTROL_PLANE_ONLY"] == "0" and settings["DELIVERY_CLOUD_EXECUTION"] == "0"
    # the same rules refuse settings without the token, and the real messages carry no value
    broken = {k: v for k, v in settings.items() if k != "INTERVIEW_ENGINE_TOKEN"}
    problems = entry.check_env(broken)
    assert problems and all("SECRETPASS" not in p for p in problems)


def test_the_engine_token_is_fresh_private_and_never_the_live_one(tmp_path):
    live = ["DELIVERY_DATABASE_URL=x", "INTERVIEW_ENGINE_TOKEN=live-production-token-0123456789abcdef"]
    a = _settings(tmp_path, live)["INTERVIEW_ENGINE_TOKEN"]
    b = _settings(tmp_path, live)["INTERVIEW_ENGINE_TOKEN"]
    assert a != b and len(a) == 48 and a != "local-interview-engine-token" and "live-production" not in a + b
    assert all(c in "0123456789abcdef" for c in a)


def test_the_engine_token_is_not_printed_and_the_settings_check_runs_on_the_real_file(env):
    api, e, log, _ = env
    out = _run(api, e)
    assert out.returncode == 0, out.stdout + out.stderr
    token = [l for l in Path(str(log) + ".env").read_text().splitlines() if l.startswith("INTERVIEW_ENGINE_TOKEN=")][0].split("=", 1)[1]
    assert len(token) == 48 and token not in out.stdout + out.stderr and "live-production" not in Path(str(log) + ".env").read_text()
    assert "check_env" in log.read_text()


def test_a_setting_the_image_refuses_stops_the_preview_with_names_only(env):
    api, e, log, _ = env
    for contract in ("CONTRACT-PROBLEMS\\nproblem: INTERVIEW_ENGINE_TOKEN must be set", "garbage"):
        out = _run(api, e, CONTRACT_OUT=contract, FAKE_SECRET="")
        assert "ABORTED [NOT-STARTABLE]" in out.stdout and "Fix:" in out.stdout and "Then run:" in out.stdout and "PREVIEW READY" not in out.stdout
        assert "SECRETPASS" not in out.stdout + out.stderr
    assert not [l for l in log.read_text().splitlines() if l.startswith("docker run -d")]


def _guard_request(origin):
    from types import SimpleNamespace
    return SimpleNamespace(method="POST", headers={"origin": origin} if origin else {}, cookies={}, url=SimpleNamespace(path="/api/auth/signin"),
                           base_url="http://127.0.0.1:18765/")


def _guard(monkeypatch, public_origin, origin, alt=frozenset()):
    from delivery_api import auth, config
    monkeypatch.setattr(auth, "enabled", lambda: True)
    monkeypatch.setattr(config, "PUBLIC_ORIGIN", public_origin)
    monkeypatch.setattr(config, "ALT_ORIGINS", alt)
    monkeypatch.setattr(config, "CLOUD_TENANTS", False)
    try:
        auth.csrf_guard(_guard_request(origin))
        return None
    except Exception as e:  # HTTPException
        return e


def test_the_preview_uses_its_own_origin_on_its_own_port_not_the_live_one(tmp_path):
    live = ["DELIVERY_DATABASE_URL=x", "DELIVERY_PUBLIC_ORIGIN=https://15-235-101-85.sslip.io"]
    os.environ["PREVIEW_PORT"] = "18765"
    try:
        settings = _settings(tmp_path, live)
    finally:
        del os.environ["PREVIEW_PORT"]
    assert settings["DELIVERY_PUBLIC_ORIGIN"] == "http://127.0.0.1:18765"
    assert (tmp_path / "out.env").read_text().count("DELIVERY_PUBLIC_ORIGIN=") == 1
    assert "sslip.io" not in (tmp_path / "out.env").read_text()


def test_the_real_guard_accepts_only_the_preview_origin_with_the_generated_settings(tmp_path, monkeypatch):
    os.environ["PREVIEW_PORT"] = "18765"
    try:
        settings = _settings(tmp_path, ["DELIVERY_PUBLIC_ORIGIN=https://15-235-101-85.sslip.io"])
    finally:
        del os.environ["PREVIEW_PORT"]
    own = settings["DELIVERY_PUBLIC_ORIGIN"]
    assert _guard(monkeypatch, own, "http://127.0.0.1:18765") is None
    for bad in ("https://15-235-101-85.sslip.io", "http://localhost:18765", "http://127.0.0.1:8000", "http://127.0.0.1", "https://evil.example"):
        err = _guard(monkeypatch, own, bad)
        assert err is not None and err.status_code == 403, bad


def test_the_live_guard_is_unchanged_and_the_rejection_explains_itself(monkeypatch):
    live = "https://15-235-101-85.sslip.io"
    assert _guard(monkeypatch, live, live) is None
    err = _guard(monkeypatch, live, "http://127.0.0.1:18765")
    assert err.status_code == 403 and err.detail["error"] == "Cross-site request rejected."
    assert "different address" in err.detail["cause"] and "official address" in err.detail["next"]
    assert "audit" not in str(err.detail).lower() and "server log" not in str(err.detail).lower()


def test_the_preview_stops_if_its_origin_is_not_its_own_address(env):
    api, e, log, _ = env
    out = _run(api, e)
    assert out.returncode == 0, out.stdout + out.stderr
    lines = [l for l in Path(str(log) + ".env").read_text().splitlines() if l.startswith("DELIVERY_PUBLIC_ORIGIN=")]
    assert lines == ["DELIVERY_PUBLIC_ORIGIN=http://127.0.0.1:18765"]
    assert "sslip.io" not in Path(str(log) + ".env").read_text() and "Open it as http://127.0.0.1:18765 or http://localhost:18765" in out.stdout


def _preview_settings(tmp_path, live):
    os.environ["PREVIEW_PORT"] = "18765"
    try:
        return _settings(tmp_path, live)
    finally:
        del os.environ["PREVIEW_PORT"]


def test_the_preview_allows_exactly_localhost_on_its_own_port_and_drops_any_live_value(tmp_path):
    settings = _preview_settings(tmp_path, ["DELIVERY_DATABASE_URL=x", "DELIVERY_ALT_ORIGINS=https://evil.example,http://localhost", "DELIVERY_PUBLIC_ORIGIN=https://15-235-101-85.sslip.io"])
    text = (tmp_path / "out.env").read_text()
    assert settings["DELIVERY_ALT_ORIGINS"] == "http://localhost:18765" and text.count("DELIVERY_ALT_ORIGINS=") == 1
    assert "evil.example" not in text and "sslip.io" not in text and settings["DELIVERY_PUBLIC_ORIGIN"] == "http://127.0.0.1:18765"


def test_the_real_guard_accepts_both_preview_aliases_on_the_port_and_nothing_else(tmp_path, monkeypatch):
    from delivery_api import config
    settings = _preview_settings(tmp_path, ["DELIVERY_PUBLIC_ORIGIN=https://15-235-101-85.sslip.io"])
    alt = config.alt_origins(settings)
    assert alt == {"http://localhost:18765"}
    own = settings["DELIVERY_PUBLIC_ORIGIN"]
    for good in ("http://127.0.0.1:18765", "http://localhost:18765"):
        assert _guard(monkeypatch, own, good, alt) is None, good
    for bad in ("https://15-235-101-85.sslip.io", "http://localhost:8000", "http://127.0.0.1:8000", "http://localhost", "https://localhost:18765",
                "http://localhost:18765.evil.example", "http://evil.localhost:18765", "http://[::1]:18765", "https://evil.example", "null"):
        err = _guard(monkeypatch, own, bad, alt)
        assert err is not None and err.status_code == 403, bad


def test_the_second_address_is_ignored_outside_the_preview_so_live_stays_strict(monkeypatch):
    from delivery_api import config
    live_env = {"DELIVERY_ALT_ORIGINS": "http://localhost:8000"}
    assert config.alt_origins(live_env) == frozenset() and config.alt_origins({**live_env, "DELIVERY_PREVIEW": "0"}) == frozenset()
    assert config.alt_origins({**live_env, "DELIVERY_PREVIEW": "1"}) == {"http://localhost:8000"}
    live = "https://15-235-101-85.sslip.io"
    assert _guard(monkeypatch, live, live, config.alt_origins(live_env)) is None
    assert _guard(monkeypatch, live, "http://localhost:8000", config.alt_origins(live_env)).status_code == 403


def test_the_server_names_the_four_repositories_of_the_running_image(monkeypatch):
    from delivery_api import app as app_module
    monkeypatch.setenv("REQUI_BUILD_ID", "aaaaaaaa-bbbbbbbb-cccccccc-dddddddd")
    monkeypatch.setenv("REQUI_BUILD_REPOS", "delivery-api=aaaaaaaa,interview-engine=bbbbbbbb,spec-generator=cccccccc,verification-loop=dddddddd")
    v = app_module._runtime_version()
    assert v["runtime_id"] == "aaaaaaaa-bbbbbbbb-cccccccc-dddddddd"
    assert v["repositories"] == {"delivery-api": "aaaaaaaa", "interview-engine": "bbbbbbbb", "spec-generator": "cccccccc", "verification-loop": "dddddddd"}
    monkeypatch.setenv("REQUI_BUILD_REPOS", "delivery-api=aaaaaaaa,Bad Name=bbbbbbbb,x=not-hex,secret=$(id),,=")
    assert app_module._runtime_version()["repositories"] == {"delivery-api": "aaaaaaaa"}
    monkeypatch.delenv("REQUI_BUILD_REPOS")
    assert app_module._runtime_version() == {"runtime_id": "aaaaaaaa-bbbbbbbb-cccccccc-dddddddd", "repositories": {}, "manifest": None}
    # built_at rides only when the build script baked one in - old images omit the key.
    monkeypatch.setenv("REQUI_BUILD_AT", "2026-10-27T07:00:00Z")
    assert app_module._runtime_version()["built_at"] == "2026-10-27T07:00:00Z"


def test_the_preview_prints_both_addresses_and_the_four_commits_it_runs(env):
    api, e, log, _ = env
    out = _run(api, e)
    assert out.returncode == 0, out.stdout + out.stderr
    envtext = Path(str(log) + ".env").read_text()
    assert [l for l in envtext.splitlines() if l.startswith("DELIVERY_ALT_ORIGINS=")] == ["DELIVERY_ALT_ORIGINS=http://localhost:18765"]
    line = [l for l in out.stdout.splitlines() if l.startswith("Running build:")][0]
    for name in ("delivery-api", "interview-engine", "spec-generator", "verification-loop"):
        assert name in line
    assert "http://localhost:18765" in out.stdout


def _check_lines(out):
    return [l.strip() for l in out.stdout.splitlines() if l.startswith("  ") and ("state file" in l or "VMODEL_ARTIFACTS_DIR" in l)]


def test_without_a_session_name_there_is_no_check(env):
    api, e, log, _ = env
    out = _run(api, e)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "Session check" not in out.stdout and "http-session.json" not in log.read_text()


def test_the_session_check_prints_only_yes_or_no_and_never_calls_absence_a_loss(env):
    api, e, log, _ = env
    out = _run(api, e, PREVIEW_CHECK_SESSION="c975886b-50d6-49c7-b51b-8c86bab28e8d")
    assert out.returncode == 0, out.stdout + out.stderr
    assert _check_lines(out) == ["engine state file for the session you named, on the server: no",
                                 "engine state file for it in this preview's copy: no",
                                 "live settings define VMODEL_ARTIFACTS_DIR: no; differs from the image default /data/artifacts: no"]
    assert "says nothing about any other place" in out.stdout
    for word in ("lost", "gone", "cannot resume", "nothing running"):
        assert word not in out.stdout.lower().split("session check")[1], word
    ssh = [l for l in log.read_text().splitlines() if "http-session.json" in l]
    assert ssh == ["ssh sudo -n test -f /data/requi/artifacts/c975886b-50d6-49c7-b51b-8c86bab28e8d/interview/http-session.json && echo YES || echo NO"]


def test_the_session_check_sees_the_file_on_both_sides_without_reading_it(env):
    api, e, log, _ = env
    sid = "abc123"
    out = _run(api, e, PREVIEW_CHECK_SESSION=sid, FAKE_SESSION_SERVER="YES", FAKE_COPY_FILE="artifacts/%s/interview/http-session.json" % sid)
    assert out.returncode == 0, out.stdout + out.stderr
    assert _check_lines(out)[:2] == ["engine state file for the session you named, on the server: yes", "engine state file for it in this preview's copy: yes"]
    assert "CONTENTS-MUST-NOT-PRINT" not in out.stdout + out.stderr


@pytest.mark.parametrize("bad", ["../x", ".", "..", "a/b", ".hidden", "a..b", "a b", "x;id", "", "a" * 81])
def test_a_session_name_that_is_not_a_plain_id_is_refused_before_any_ssh(env, bad):
    api, e, log, _ = env
    out = _run(api, e, PREVIEW_CHECK_SESSION=bad)
    if bad == "":
        assert out.returncode == 0 and "Session check" not in out.stdout
        return
    assert out.returncode != 0 and "ABORTED [CHECK-ID]" in out.stdout and "Fix:" in out.stdout and "Then run:" in out.stdout
    assert "ssh" not in log.read_text() and "PREVIEW READY" not in out.stdout


@pytest.mark.parametrize("value,defined,differs", [("/data/artifacts", "yes", "no"), ("/data/other", "yes", "yes"), ('"/data/artifacts"', "yes", "no")])
def test_the_session_check_says_whether_the_live_settings_move_the_artifacts_folder(env, value, defined, differs):
    api, e, log, _ = env
    live = Path(e["FAKE_ENV"])
    live.write_text(live.read_text() + "VMODEL_ARTIFACTS_DIR=%s\n" % value)
    out = _run(api, e, PREVIEW_CHECK_SESSION="abc")
    assert out.returncode == 0, out.stdout + out.stderr
    assert _check_lines(out)[2] == "live settings define VMODEL_ARTIFACTS_DIR: %s; differs from the image default /data/artifacts: %s" % (defined, differs)
    assert "/data/other" not in out.stdout


def test_preview_banner_does_not_call_execution_preview_read_only_or_off():
    import json
    uri=(WEB/'chrome.js').as_uri()
    source=f"import {{previewBannerText}} from '{uri}'; console.log(previewBannerText({{preview:true,execution_enabled:true}}))"
    result=subprocess.run(['node','--input-type=module','-e',source],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert 'enabled by server setting' in result.stdout and 'changes here can affect live data' in result.stdout
    assert 'execution off' not in result.stdout and 'read-only' not in result.stdout
