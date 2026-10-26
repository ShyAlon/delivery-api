"""Hermetic tests for scripts/ops/preview-rebuild.sh.

docker and git are stubbed on PATH (no daemon, no network, no real checkout);
the handover target is a stub via PREVIEW_EXEC_SCRIPT. The script must remove
a leftover container, gate on a typed yes only when the preview is RUNNING,
fast-forward the four checkouts, prune old requi-full tags but keep the
candidate tag, and hand over to preview-exec.
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'scripts' / 'ops' / 'preview-rebuild.sh'
CANDIDATE = 'requi-full:00000000-00000000-00000000-00000000'

GIT_STUB = '''#!/usr/bin/env bash
while [ $# -gt 0 ]; do case "$1" in -C) shift 2;; *) break;; esac; done
case "$1" in
  remote) echo https://github.com/ShyAlon/delivery-api;;
  fetch|pull|checkout) exit 0;;
  status) [ -f "$GIT_STATE/dirty" ] && echo " M dirty-file"; exit 0;;
  rev-list) echo 0;;
  diff) exit 0;;
  rev-parse) case "$2" in --abbrev-ref) echo main;; --short=8) echo 00000000;; --is-inside-work-tree) echo true;; *) echo 0000000000000000000000000000000000000000;; esac;;
esac
exit 0
'''

DOCKER_STUB = '''#!/usr/bin/env bash
echo "$*" >> "$DOCKER_LOG"
case "$1" in
  info) exit 0;;
  ps)
    if [ "$2" = -a ]; then [ -f "$DOCKER_STATE/container" ] && echo requi-preview-exec
    else [ -f "$DOCKER_STATE/running" ] && echo requi-preview-exec; fi
    exit 0;;
  images) [ -f "$DOCKER_STATE/images" ] && cat "$DOCKER_STATE/images"; exit 0;;
  rm|rmi) exit 0;;
esac
exit 0
'''

def _stubbin(tmp_path):
    d = tmp_path / 'bin'
    if not d.exists():
        d.mkdir()
        (d / 'docker').write_text(DOCKER_STUB)
        (d / 'git').write_text(GIT_STUB)
        for f in d.iterdir():
            f.chmod(0o755)
    return d

def _run(tmp_path, container=False, running=False, images=(), dirty=False, tty='yes\n', env_extra=None):
    root = tmp_path / 'repos'
    for r in ('delivery-api', 'interview-engine', 'spec-generator', 'verification-loop'):
        (root / r / '.git').mkdir(parents=True, exist_ok=True)
    state = tmp_path / 'dstate'; state.mkdir(exist_ok=True)
    if container: (state / 'container').touch()
    if running: (state / 'running').touch()
    if images: (state / 'images').write_text('\n'.join(images) + '\n')
    gstate = tmp_path / 'gstate'; gstate.mkdir(exist_ok=True)
    if dirty: (gstate / 'dirty').touch()
    handover = tmp_path / 'handover.sh'
    handover.write_text('#!/usr/bin/env bash\necho PREVIEW-EXEC-REACHED\n')
    handover.chmod(0o755)
    ttyfile = tmp_path / 'tty'; ttyfile.write_text(tty)
    log = tmp_path / 'docker.log'
    env = {'PATH': '%s:/usr/bin:/bin' % _stubbin(tmp_path), 'HOME': str(tmp_path),
           'REQUI_REPOS_ROOT': str(root), 'DOCKER_STATE': str(state), 'GIT_STATE': str(gstate),
           'DOCKER_LOG': str(log), 'PREVIEW_EXEC_SCRIPT': str(handover), 'PREVIEW_TTY': str(ttyfile)}
    if env_extra: env.update(env_extra)
    proc = subprocess.run(['bash', str(SCRIPT)], capture_output=True, text=True, cwd=REPO, env=env, timeout=30)
    calls = log.read_text() if log.exists() else ''
    return proc, calls

def test_provider_mode_is_printed_before_handover(tmp_path):
    out, _ = _run(tmp_path)
    assert '== provider: preview-exec will ask you to pick' in out.stdout, out.stdout
    out, _ = _run(tmp_path, env_extra={'PREVIEW_EXEC_PROVIDER': 'deepseek'})
    assert '== provider: deepseek (from PREVIEW_EXEC_PROVIDER)' in out.stdout, out.stdout
    out, _ = _run(tmp_path, env_extra={'PREVIEW_EXEC_PROVIDER': 'ollama'})
    assert '== provider: ollama (from PREVIEW_EXEC_PROVIDER)' in out.stdout, out.stdout

def test_leftover_container_removed_and_old_tags_pruned_candidate_kept(tmp_path):
    p, calls = _run(tmp_path, container=True, images=['requi-full:old1', 'requi-full:old2', CANDIDATE])
    assert p.returncode == 0, p.stdout[-500:]
    assert 'PREVIEW-EXEC-REACHED' in p.stdout
    assert 'rm -f requi-preview-exec' in calls
    assert 'rmi requi-full:old1' in calls and 'rmi requi-full:old2' in calls
    assert 'rmi %s' % CANDIDATE not in calls

def test_running_container_requires_typed_yes(tmp_path):
    p, calls = _run(tmp_path, container=True, running=True, tty='no\n')
    assert p.returncode == 1
    assert 'ABORTED [NOT-CONFIRMED]' in p.stdout
    assert 'rm -f requi-preview-exec' not in calls
    assert 'PREVIEW-EXEC-REACHED' not in p.stdout
    p, calls = _run(tmp_path, container=True, running=True, tty='yes\n')
    assert p.returncode == 0, p.stdout[-500:]
    assert 'rm -f requi-preview-exec' in calls
    assert 'PREVIEW-EXEC-REACHED' in p.stdout

def test_no_container_no_images_goes_straight_to_handover(tmp_path):
    p, calls = _run(tmp_path)
    assert p.returncode == 0, p.stdout[-500:]
    assert 'PREVIEW-EXEC-REACHED' in p.stdout
    assert 'rm -f' not in calls and 'rmi' not in calls

def test_dangling_none_tag_is_never_removed(tmp_path):
    p, calls = _run(tmp_path, images=['requi-full:<none>', CANDIDATE])
    assert p.returncode == 0, p.stdout[-500:]
    assert 'rmi' not in calls

def test_dirty_checkout_aborts_before_any_removal(tmp_path):
    p, calls = _run(tmp_path, container=True, images=['requi-full:old1'], dirty=True)
    assert p.returncode == 1
    assert 'ABORTED [DIRTY' in p.stdout           # ops_start or the per-repo loop, both fail closed
    assert 'rmi' not in calls and 'PREVIEW-EXEC-REACHED' not in p.stdout
