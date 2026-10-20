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
  *full-smoke.py*) exit "${FAKE_SMOKE_RC:-0}";;
  *"docker rmi"*) exit "${FAKE_RMI_RC:-0}";;
  *"--format"*"image"*|*"image inspect --format"*) echo sha256:fake;;
  *"docker info"*) echo ok;;
  *"du -sm"*) echo "${FAKE_SIZE:-5}";;
  *"image inspect"*) exit 0;;
  *"docker save"*) :;;
  *"full.env"*) cat "$FAKE_ENV";;
  *"tar -C"*) tar -cf - -T /dev/null;;
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
       case "$*" in *"python -c"*) echo DBOK;; esac;;
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
    return subprocess.run(["bash", "scripts/ops/preview.sh"], cwd=api, capture_output=True, text=True, env={**e, **extra}, timeout=60)


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
    assert "REAL one" in out.stdout and "Grant Credits" in out.stdout and "Type yes" in out.stdout


def test_execution_is_forced_off_and_no_model_key_reaches_the_container(env):
    api, e, log, _ = env
    out = _run(api, e)
    assert out.returncode == 0, out.stdout + out.stderr
    settings = Path(str(log) + ".env").read_text().splitlines()
    assert settings.count("DELIVERY_CLOUD_EXECUTION=0") == 1 and not any(l.startswith("DELIVERY_CLOUD_EXECUTION=1") for l in settings)
    assert "DELIVERY_COOKIE_SECURE=0" in settings and "DELIVERY_PREVIEW=1" in settings and "DELIVERY_COOKIE_SECURE=1" not in settings
    assert not any(w in l.upper() for l in settings for w in ("API_KEY", "DEEPSEEK", "OPENAI", "ANTHROPIC", "GEMINI", "STRIPE", "SECRETPASS_X"))
    assert all(l.startswith("DELIVERY_") for l in settings) and "DELIVERY_SUPABASE_ANON_KEY=anon" in settings
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
const made=[];const body={prepended:[],prepend(b){this.prepended.push(b)}};
globalThis.document={createElement:()=>{const b={style:{},setAttribute(){},className:'',textContent:''};made.push(b);return b},querySelector:()=>null,body};
const {addPreviewBanner}=await import(%r);
addPreviewBanner({preview:false});addPreviewBanner({});addPreviewBanner(null);
const none=body.prepended.length;
addPreviewBanner({preview:true});
console.log(JSON.stringify({none,one:body.prepended.length,text:body.prepended[0].textContent}));
""" % (WEB / "chrome.js").as_uri()
    r = subprocess.run(["node", "--input-type=module", "-e", src], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out == {"none": 0, "one": 1, "text": "Preview: real data, execution off"}


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
    assert text.index("umask 077; WORK=$(mktemp -d)") < text.index('full.env" > "$WORK/live.env"')
    r = subprocess.run(["bash", "-c", 'umask 022; PREVIEW_FUNCTIONS_ONLY=1 . %s; printf "DELIVERY_A=1\nOPENAI_KEY=x\n" > /tmp/in.$$; build_env /tmp/in.$$ /tmp/out.$$; stat -c %%a /tmp/out.$$ 2>/dev/null || stat -f %%Lp /tmp/out.$$; cat /tmp/out.$$; rm -f /tmp/in.$$ /tmp/out.$$' % (OPS / "preview.sh")],
                       capture_output=True, text=True)
    lines = r.stdout.split()
    assert lines[0] == "600" and "DELIVERY_A=1" in lines and not any("OPENAI" in l for l in lines)



def test_an_existing_image_is_smoke_checked_before_reuse_and_not_rebuilt_when_it_passes(env):
    api, e, log, _ = env
    assert _run(api, e).returncode == 0
    text = log.read_text()
    assert "full-smoke.py" in text and "BUILD-SHOULD-NOT-RUN" not in text


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
