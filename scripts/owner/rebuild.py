#!/usr/bin/env python3
"""Owner rebuild ritual: the manual six-repo rebuild sequence as one command.

Default is a dry review. --apply runs the sequence in order:
  1. req status              read-only state of every sibling checkout
  2. scripts/pull-all.sh     pull the six checkouts and sync their environments
  3. req image --review      check exact-image inputs, nothing built
  4. req image --accept-local  build and exercise the exact local image
  5. req migrate             dry review, then apply only after the owner types
                             "APPLY MIGRATIONS" (or "SKIP" to continue without)
  6. preview restart         stop+rename the old preview container, then
                             req preview --apply
  7. cleanup                 list old renamed preview containers and dangling
                             or superseded candidate images; delete only after
                             the owner types the exact quoted string. The
                             running preview and the previous renamed container
                             (and their images) are kept by default.

Every typed confirmation inside req image --accept-local, req migrate --apply
and req preview --apply stays the owner's gate: this command never answers,
pipes or pre-fills a confirmation. Completed steps are recorded in a state
file keyed by the six origin/main revisions plus the runtime and acceptance
contracts; a re-run revalidates and skips completed steps.

Server dependencies: GitHub (git fetch/pull for the six sibling checkouts,
gh auth status). Docker is the local daemon. req migrate reaches the owner
database only through its own reviewed script with its own typed
confirmations. No registry, application server or paid provider is contacted.
"""
from __future__ import annotations
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts' / 'docker'))
from image_receipt import digest  # noqa: E402
from image_workflow import WorkflowError, acceptance_contract, command, log  # noqa: E402
from runtime_spec import contract_bytes  # noqa: E402

# Keep in sync with REPOS in scripts/pull-all.sh; a test asserts the match.
REPOS = ('delivery-api', 'interview-engine', 'spec-generator', 'vmodel-contracts', 'verification-loop', 'vmodel-spec-app')
# The four source checkouts the exact-image receipt pins (image_workflow.REPOS).
IMAGE_REPOS = ('delivery-api', 'interview-engine', 'spec-generator', 'verification-loop')
PREVIEW_NAME = 'requi-preview-exec'
ACCEPTANCE_CHECKS = ('startup', 'signed-http', 'child-lifecycle', 'stop', 'retained-outputs')
STEPS = ('pull-all', 'accept-local', 'migrate-apply', 'preview-restart', 'cleanup')
STATE_SCHEMA = 1


def origin_shas(repos_root: Path, repos=REPOS, run=command) -> dict:
    return {name: run(['git', '-C', str(repos_root / name), 'rev-parse', 'origin/main']) for name in repos}


def checkout_heads(repos_root: Path, repos=REPOS, run=None) -> dict:
    run = run or command
    return {name: run(['git', '-C', str(repos_root / name), 'rev-parse', 'HEAD']) for name in repos}


def fetched_origin_shas(repos_root: Path, repos=REPOS, run=None) -> dict:
    run = run or command
    for name in repos:
        run(['git', '-C', str(repos_root / name), 'fetch', 'origin', 'main'], timeout=120)
    return origin_shas(repos_root, repos, run=run)


def reexec_front_end(root: Path) -> None:
    # The req front-end fast-forwards delivery-api, then starts the new rebuild.py.
    os.execv('/bin/bash', ['bash', str(root / 'scripts/ops/req'), 'rebuild', '--apply'])


def run_key(shas: dict, runtime_contract: str, acceptance: str) -> str:
    text = ''.join(name + '=' + shas[name] + '\n' for name in sorted(shas))
    return digest((text + runtime_contract + '\n' + acceptance + '\n').encode())


def load_state(path: Path, key: str) -> dict:
    fresh = {'schema': STATE_SCHEMA, 'key': key, 'steps': {}}
    if not path.exists():
        return fresh
    try:
        value = json.loads(path.read_text())
    except ValueError as error:
        raise WorkflowError('rebuild state is not valid JSON; inspect it before retrying: ' + str(path)) from error
    if set(value) != {'schema', 'key', 'steps'} or value['schema'] != STATE_SCHEMA or type(value['steps']) is not dict:
        raise WorkflowError('invalid rebuild state; inspect it before retrying: ' + str(path))
    if set(value['steps']) - set(STEPS):
        raise WorkflowError('unknown step in rebuild state; inspect it before retrying: ' + str(path))
    if value['key'] != key:
        log('INFO', 'sources or contracts changed since the recorded run; starting a fresh sequence')
        return fresh
    return value


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, sort_keys=True, indent=2))
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def mark_complete(state_path: Path, state: dict, name: str, evidence: dict) -> None:
    if name not in STEPS:
        raise WorkflowError('unknown rebuild step: ' + name)
    state['steps'][name] = {'completed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'evidence': evidence}
    save_state(state_path, state)


def typed(prompt: str, tty_path: str = '/dev/tty') -> str:
    # The waiting prompt is the last visible line before input blocks, and the
    # answer can only come from the terminal, never from a pipe into this command.
    log('INFO', 'Waiting for input: ' + prompt)
    try:
        with open(tty_path) as terminal:
            line = terminal.readline()
    except OSError as error:
        raise WorkflowError('typed confirmation needs an interactive terminal; nothing confirmed') from error
    if line == '':
        raise WorkflowError('input ended; nothing confirmed')
    return line.rstrip('\n').strip()


def run_child(args, *, interruptible: bool = False, popen=subprocess.Popen) -> None:
    # The child inherits stdin/stdout/stderr: every confirmation inside it is
    # answered by the owner at the terminal, never by this command.
    log('INFO', 'running: ' + ' '.join(str(a) for a in args))
    process = popen([str(a) for a in args])
    try:
        code = process.wait()
    except KeyboardInterrupt:
        # SIGINT reaches the whole foreground group; the child handles it first.
        code = process.wait()
        if not interruptible or code != 0:
            raise WorkflowError('interrupted and the step did not complete: ' + ' '.join(str(a) for a in args)) from None
    if code != 0:
        raise WorkflowError('step command failed with exit code ' + str(code) + ': ' + ' '.join(str(a) for a in args))


def venv_check(root: Path) -> None:
    """The exact venv gate the owner steps rely on, moved to the front.

    pull-all.sh syncs runtime dependencies only: `uv sync --locked` without
    --extra dev, or `pip install -e .` without .[dev]. pytest, psycopg and the
    vendored contracts live in the dev optional-dependency extra, so a
    successful sync can still leave req migrate aborting at venv_check.py
    mid-sequence. Running the same gate here fails fast with its exact
    per-module repair line before any step runs.
    """
    result = subprocess.run([str(root / '.venv/bin/python'), str(root / 'scripts/ops/venv_check.py')])
    if result.returncode:
        raise WorkflowError('venv dependencies are missing; run the exact install line printed above, then rerun this command')


def preflight(root: Path, repos_root: Path, *, which=shutil.which, run=command, tty_path: str = '/dev/tty') -> dict:
    """Fail fast, cheapest and likeliest-failing checks first; name every miss."""
    if not (root / '.git').is_dir():
        raise WorkflowError('not a git checkout: ' + str(root))
    if root.name != 'delivery-api':
        raise WorkflowError("checkout folder must be named 'delivery-api'; pull-all.sh and req resolve siblings by name: " + str(root))
    try:
        descriptor = os.open(tty_path, os.O_RDWR)
        os.close(descriptor)
    except OSError as error:
        raise WorkflowError('typed confirmations need an interactive terminal; nothing ran') from error
    for tool in ('git', 'docker', 'gh', 'bash'):
        if not which(tool):
            raise WorkflowError('missing local tool: ' + tool)
    if not (root / '.venv/bin/python').is_file():
        raise WorkflowError('missing .venv/bin/python; install current checkout dependencies first')
    venv_check(root)
    run(['docker', 'info', '--format', '{{.OSType}}'])  # Docker daemon running
    run(['gh', 'auth', 'status'])  # GitHub authentication present
    for path in (root / 'scripts/ops/req', root / 'scripts/ops/req-owner.sh', root / 'scripts/pull-all.sh'):
        if not path.is_file():
            raise WorkflowError('missing repo script: ' + str(path))
    expected = (Path(os.environ.get('REQUI_ROOT', str(Path.home() / 'development/require'))) / 'delivery-api').resolve()
    if expected != root.resolve():
        raise WorkflowError('nested req resolves delivery-api at ' + str(expected) + ' but this checkout is ' + str(root)
                            + '; rerun with REQUI_ROOT=' + str(repos_root))
    for name in REPOS:
        if not (repos_root / name / '.git').is_dir():
            raise WorkflowError('missing sibling checkout: ' + name + ' at ' + str(repos_root / name))
    for name in REPOS:  # server round trips last
        run(['git', '-C', str(repos_root / name), 'fetch', 'origin', 'main'], timeout=120)
    if run(['git', '-C', str(root), 'branch', '--show-current']) != 'main':
        raise WorkflowError('delivery-api checkout is not on main')
    if run(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=no']):
        raise WorkflowError('tracked changes in delivery-api; commit or stash them first')
    if run(['git', '-C', str(root), 'rev-parse', 'HEAD']) != run(['git', '-C', str(root), 'rev-parse', 'origin/main']):
        raise WorkflowError('delivery-api is not current main; the req front-end syncs it before owner commands')
    return origin_shas(repos_root, run=run)


def accepted_receipt(ctx) -> dict | None:
    """The durable accepted-image receipt for the current sources and contracts.

    Reuse matches local_image_preview, which reads the same local-acceptance
    stage across invocations; here the image must also still exist locally.
    """
    base = ctx['home'] / '.local/share/requi/image-workflow'
    if not base.is_dir():
        return None
    expected_sources = {name: ctx['shas'][name] for name in IMAGE_REPOS}
    for state_file in sorted(base.glob('*/state.json'), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            stages = json.loads(state_file.read_text()).get('stages', {})
            accepted = stages.get('local-acceptance', {})
            receipt = json.loads((state_file.parent / 'accepted-image.json').read_bytes())
        except (ValueError, OSError) as error:
            log('WARN', 'ignoring unreadable image-workflow run dir ' + str(state_file.parent) + ': ' + str(error))
            continue
        if accepted.get('checks') != {name: 'passed' for name in ACCEPTANCE_CHECKS}:
            continue
        if accepted.get('runtime_contract') != ctx['runtime_contract']:
            continue
        image_id = accepted.get('image_id')
        if not image_id or receipt.get('config_digest') != image_id:
            continue
        if receipt.get('sources') != expected_sources:
            continue
        if receipt.get('runtime_contract') != ctx['runtime_contract'] or receipt.get('acceptance_contract') != ctx['acceptance_contract']:
            continue
        if not ctx['image_exists'](image_id):
            continue
        return {'image_id': image_id, 'run_dir': str(state_file.parent)}
    return stage_evidence_receipt(ctx)


def stage_evidence_receipt(ctx) -> dict | None:
    """Local acceptance proven by the run's own stage evidence, no publish receipt.

    req image --accept-local never writes accepted-image.json - that file is the
    publication receipt and only --publish produces it. A local run proves itself
    in state.json instead: the run directory name and its input_key are both
    digests of the exact inputs (four source revisions, platform, runtime and
    acceptance contracts), so recomputing them here binds every contract without
    trusting any stored success boolean.
    """
    inputs = {'sources': {name: ctx['shas'][name] for name in IMAGE_REPOS},
              'platform': 'linux/amd64', 'runtime_contract': ctx['runtime_contract'],
              'acceptance_contract': ctx['acceptance_contract']}
    run_dir = ctx['home'] / '.local/share/requi/image-workflow' / digest(
        json.dumps(inputs, sort_keys=True).encode()).split(':')[1][:24]
    try:
        value = json.loads((run_dir / 'state.json').read_text())
    except (ValueError, OSError):
        return None
    if value.get('input_key') != digest(json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()):
        return None
    stages = value.get('stages', {})
    built, accepted = stages.get('build', {}), stages.get('local-acceptance', {})
    if built.get('sources') != inputs['sources'] or built.get('platform') != 'linux/amd64':
        return None
    image_id = built.get('image_id')
    if not image_id or accepted.get('image_id') != image_id:
        return None
    if accepted.get('checks') != {name: 'passed' for name in ACCEPTANCE_CHECKS}:
        return None
    if accepted.get('runtime_contract') != ctx['runtime_contract']:
        return None
    if not ctx['image_exists'](image_id):
        return None
    return {'image_id': image_id, 'run_dir': str(run_dir)}


def plan_cleanup(containers: list, image_rows: list, *, keep_previous: int, keep_image_ids: set) -> tuple:
    """Old renamed preview containers (newest keep_previous stay) plus dangling
    and superseded candidate images, minus every kept image ID."""
    renamed = sorted((c for c in containers if str(c.get('Names', '')).startswith(PREVIEW_NAME + '-')),
                     key=lambda c: str(c.get('CreatedAt', '')), reverse=True)
    kept, rest = renamed[:keep_previous], renamed[keep_previous:]
    removable = [c for c in rest if c.get('State') != 'running']
    keep = {image_key(i) for i in keep_image_ids}
    images = [i for i in image_rows
              if i.get('Repository') in ('<none>', 'requi-full-candidate') and image_key(i.get('ID')) not in keep]
    return kept, removable, images


def image_key(image_id) -> str:
    """`docker images` prints a 12-hex short id; inspect prints sha256:<64 hex>.
    Both name the same image once the prefix and length are normalized."""
    text = str(image_id or '')
    return text[7:19] if text.startswith('sha256:') else text[:12]


def containers_images(ctx, container_ids: list) -> set:
    """Full image ids used by the given containers (running or stopped)."""
    if not container_ids:
        return set()
    raw = ctx['docker'](['container', 'inspect', '--format', '{{.Image}}', *container_ids])
    return {line.strip() for line in str(raw).splitlines() if line.strip()}


def remove_item(ctx, args: list, label: str):
    """Run one docker removal; return None on success or the full reason.
    Removal output is a Docker daemon message, safe to show (unlike state reads)."""
    try:
        code, text = ctx['docker_try'](args)
    except Exception as error:  # reported, never swallowed
        return 'docker ' + ' '.join(args) + ' (' + label + ') raised ' + type(error).__name__ + ': ' + str(error)
    if code:
        return 'docker ' + ' '.join(args) + ' (' + label + ') exit ' + str(code) + ': ' + (str(text).strip() or 'no output')
    return None


def container_image(ctx, name: str):
    try:
        value = json.loads(ctx['docker'](['container', 'inspect', '--format', '{{json .}}', name]))
    except WorkflowError:
        return None
    return value.get('Image'), value.get('State', {}).get('Running') is True


def step_pull_all(ctx) -> None:
    if 'pull-all' in ctx['state']['steps'] and ctx['heads']() == ctx['shas']:
        log('PASS', 'step 2/7 reused: pull-all completed and every checkout still matches origin/main')
        return
    ctx['run_child'](['bash', str(ctx['root'] / 'scripts/pull-all.sh')])
    heads = ctx['heads']()
    for name, sha in heads.items():
        if sha != ctx['shas'][name]:
            raise WorkflowError('pull-all left ' + name + ' at ' + sha[:8] + ' instead of origin/main ' + ctx['shas'][name][:8])
    mark_complete(ctx['state_path'], ctx['state'], 'pull-all', {'heads': heads})


def step_accept_local(ctx) -> None:
    receipt = accepted_receipt(ctx)
    if 'accept-local' in ctx['state']['steps'] and receipt:
        log('PASS', 'step 4/7 reused: accepted image ' + receipt['image_id'][:19] + ' revalidated against current sources and contracts')
        ctx['receipt'] = receipt
        return
    if 'accept-local' in ctx['state']['steps']:
        log('INFO', 'recorded acceptance no longer verifies; running the stage again')
    ctx['run_child'](['bash', str(ctx['req']), 'image', '--accept-local'])
    receipt = accepted_receipt(ctx)
    if not receipt:
        raise WorkflowError('accept-local exited cleanly but no accepted receipt matches the current sources and contracts')
    mark_complete(ctx['state_path'], ctx['state'], 'accept-local', {'image_id': receipt['image_id'], 'run_dir': receipt['run_dir']})
    ctx['receipt'] = receipt


def step_migrate(ctx) -> None:
    ctx['run_child'](['bash', str(ctx['req']), 'migrate'])  # dry review, always shown
    if 'migrate-apply' in ctx['state']['steps']:
        log('PASS', 'step 5/7 reused: migrations recorded applied for these inputs')
        return
    answer = ctx['typed']('Type "APPLY MIGRATIONS" to run the owner-gated migrate apply, or "SKIP" to leave the database unchanged this run:')
    if answer == 'SKIP':
        log('WARN', 'migrations left unapplied by explicit owner choice')
        return
    if answer != 'APPLY MIGRATIONS':
        raise WorkflowError('migrate apply declined; stopping before the preview restart; rerun the same command to resume')
    ctx['run_child'](['bash', str(ctx['req']), 'migrate', '--apply'])
    mark_complete(ctx['state_path'], ctx['state'], 'migrate-apply', {'applied_after_review': True})


def step_preview_restart(ctx) -> None:
    receipt = ctx.get('receipt') or accepted_receipt(ctx)
    if not receipt:
        raise WorkflowError('no accepted image matches the current sources; the accept-local step must complete first')
    ctx['receipt'] = receipt
    current = container_image(ctx, PREVIEW_NAME)
    if 'preview-restart' in ctx['state']['steps'] and current == (receipt['image_id'], True):
        log('PASS', 'step 6/7 reused: preview already runs the accepted image ' + receipt['image_id'][:19])
        return
    if current is not None:
        _, running = current
        if running:
            ctx['docker'](['stop', PREVIEW_NAME], timeout=180)
        renamed = PREVIEW_NAME + '-' + ctx['now']().strftime('%Y%m%d-%H%M%S')
        ctx['docker'](['rename', PREVIEW_NAME, renamed])
        log('INFO', 'previous preview container renamed to ' + renamed + '; kept for rollback and reviewed by the cleanup step')
    ctx['run_child'](['bash', str(ctx['req']), 'preview', '--apply'], interruptible=True)
    actual = container_image(ctx, PREVIEW_NAME)
    if actual != (receipt['image_id'], True):
        raise WorkflowError('preview exited cleanly but is not running the accepted image; inspect: docker logs --tail 80 ' + PREVIEW_NAME)
    mark_complete(ctx['state_path'], ctx['state'], 'preview-restart', {'image_id': receipt['image_id'], 'container': PREVIEW_NAME})


def step_cleanup(ctx) -> None:
    keep_ids = set()
    current = container_image(ctx, PREVIEW_NAME)
    if current is not None:
        keep_ids.add(current[0])
    containers = json.loads(ctx['docker'](['ps', '-a', '--filter', 'name=^' + PREVIEW_NAME, '--format', '{{json .}}'], multiline=True))
    image_rows = json.loads(ctx['docker'](['images', '--digests', '--format', '{{json .}}'], multiline=True))
    provisional = sorted((c for c in containers if str(c.get('Names', '')).startswith(PREVIEW_NAME + '-')),
                         key=lambda c: str(c.get('CreatedAt', '')), reverse=True)
    for kept in provisional[:1]:
        found = container_image(ctx, str(kept.get('ID') or kept.get('Names')))
        if found is not None:
            keep_ids.add(found[0])
    removable_ids = {str(c.get('ID')) for c in plan_cleanup(containers, [], keep_previous=1, keep_image_ids=set())[1]}
    survivors = [str(c.get('ID')) for c in containers if str(c.get('ID')) not in removable_ids]
    # Any other container (not just preview ones) that survives pins its image.
    others = [x for x in ctx['docker'](['ps', '-a', '-q']).split() if x not in removable_ids]
    keep_ids |= containers_images(ctx, sorted(set(survivors) | set(others)))
    kept, removable, images = plan_cleanup(containers, image_rows, keep_previous=1, keep_image_ids=keep_ids)
    if kept:
        log('INFO', 'cleanup keeps: the running ' + PREVIEW_NAME + ' and previous ' + ', '.join(str(k.get('Names')) for k in kept))
    if not removable and not images:
        log('PASS', 'step 7/7: nothing old to remove')
        mark_complete(ctx['state_path'], ctx['state'], 'cleanup', {'removed': []})
        return
    for container in removable:
        log('WARN', 'old preview container: ' + str(container.get('Names')) + ' created ' + str(container.get('CreatedAt')))
    for image in images:
        log('WARN', 'old image: ' + str(image.get('Repository')) + ':' + str(image.get('Tag')) + ' ' + str(image.get('ID')))
    expected = 'DELETE ' + str(len(removable)) + ' CONTAINERS AND ' + str(len(images)) + ' IMAGES'
    if ctx['typed']('Type "' + expected + '" to remove exactly the items listed above; anything else keeps them:') != expected:
        log('WARN', 'cleanup declined; every listed container and image kept')
        return
    removed, failures = [], []
    for container in removable:
        label = str(container.get('Names'))
        reason = remove_item(ctx, ['rm', str(container.get('ID'))], label)
        (failures.append(reason) if reason else removed.append(label))
    for image in images:
        label = str(image.get('Repository')) + ':' + str(image.get('Tag'))
        reason = remove_item(ctx, ['rmi', str(image.get('ID'))], label)
        (failures.append(reason) if reason else removed.append(label))
    if failures:
        raise WorkflowError('cleanup removed ' + str(len(removed)) + ' of ' + str(len(removed) + len(failures)) +
                            ' items and left cleanup incomplete; failed: ' + ' | '.join(failures))
    mark_complete(ctx['state_path'], ctx['state'], 'cleanup', {'removed': removed})


def plan(ctx) -> None:
    print('Rebuild plan for ' + str(ctx['repos_root']) + ' (state ' + str(ctx['state_path']) + '):')
    done = set(ctx['state']['steps'])
    rows = [('1', 'req status', 'read-only checkout state, always shown', False),
            ('2', 'pull-all', 'pull the six checkouts and sync environments', True),
            ('3', 'req image --review', 'check exact-image inputs, nothing built', False),
            ('4', 'req image --accept-local', 'build and exercise the exact local image', True),
            ('5', 'req migrate', 'dry review; apply only after the owner types "APPLY MIGRATIONS"', True),
            ('6', 'preview restart', 'stop+rename the old preview container, then req preview --apply', True),
            ('7', 'cleanup', 'list old preview containers and images; delete only after a typed confirm', True)]
    step_names = {'2': 'pull-all', '4': 'accept-local', '5': 'migrate-apply', '6': 'preview-restart', '7': 'cleanup'}
    for number, name, what, gated in rows:
        mark = 'recorded complete' if gated and step_names[number] in done else ('owner gate' if gated else 'read-only')
        print('  ' + number + '. ' + name.ljust(26) + what + '  [' + mark + ']')
    print('Server dependencies: GitHub (git fetch/pull, gh auth status) and the local Docker daemon.')
    print('No registry, application server or paid provider is contacted. Owner typed confirmations stay his gates.')


MAX_RESTARTS = 3


def ensure_current(ctx, where: str) -> None:
    """Every child `req` re-fetches and fast-forwards main, and image_workflow
    refuses a checkout behind origin/main. If main moved after this run pinned
    its revisions (a merge during the build or a long typed prompt), the accepted
    image no longer matches the sources the next child sees. Restart through the
    req front-end (fresh code, fresh contracts) instead of failing in a child."""
    latest = ctx['latest_origin']()
    moved = sorted(name for name, sha in latest.items() if sha != ctx['shas'].get(name))
    if not moved:
        return
    detail = ', '.join(name + ' ' + str(ctx['shas'].get(name))[:8] + ' -> ' + sha[:8] for name, sha in sorted(latest.items()) if name in moved)
    count = int(os.environ.get('REQUI_REBUILD_RESTARTS', '0'))
    if count >= MAX_RESTARTS:
        raise WorkflowError('origin/main kept moving (' + str(count) + ' restarts); stopped before ' + where + ': ' + detail
                            + '; rerun the same command once merges settle')
    os.environ['REQUI_REBUILD_RESTARTS'] = str(count + 1)
    log('WARN', 'origin/main moved during this run before ' + where + ' (' + detail
        + '); restarting the sequence on the new revisions so the accepted image matches')
    ctx['reexec']()


def apply(ctx) -> None:
    ctx['run_child'](['bash', str(ctx['req']), 'status'])
    step_pull_all(ctx)
    ctx['run_child'](['bash', str(ctx['req']), 'image', '--review'])
    ensure_current(ctx, 'accept-local')
    step_accept_local(ctx)
    ensure_current(ctx, 'migrate')
    step_migrate(ctx)
    ensure_current(ctx, 'preview restart')
    step_preview_restart(ctx)
    step_cleanup(ctx)
    log('PASS', 'rebuild sequence complete; every owner gate was answered at the terminal')


def build_ctx(root: Path) -> dict:
    repos_root = root.parent
    shas = preflight(root, repos_root)
    runtime_contract = digest(contract_bytes())
    acceptance = acceptance_contract(root)

    def docker(args, timeout=120, multiline=False):
        raw = command(['docker', *[str(a) for a in args]], timeout=timeout)
        return '[' + ','.join(raw.splitlines()) + ']' if multiline else raw

    def docker_try(args, timeout=120):
        result = subprocess.run(['docker', *[str(a) for a in args]], capture_output=True, text=True, timeout=timeout, check=False)
        return result.returncode, (result.stderr or result.stdout)

    def image_exists(image_id: str) -> bool:
        try:
            command(['docker', 'image', 'inspect', '--format', '{{.Id}}', image_id])
            return True
        except WorkflowError:
            return False

    ctx = {'root': root, 'repos_root': repos_root, 'req': root / 'scripts/ops/req',
           'home': Path.home(), 'shas': shas, 'runtime_contract': runtime_contract, 'acceptance_contract': acceptance,
           'state_path': Path(os.environ.get('REQUI_REBUILD_STATE', str(Path.home() / '.local/share/requi/rebuild/state.json'))),
           'typed': typed, 'run_child': run_child, 'docker': docker, 'docker_try': docker_try, 'image_exists': image_exists,
           'heads': lambda: checkout_heads(repos_root), 'latest_origin': lambda: fetched_origin_shas(repos_root),
           'reexec': lambda: reexec_front_end(root), 'now': datetime.datetime.now}
    key = run_key(shas, runtime_contract, acceptance)
    ctx['state'] = load_state(ctx['state_path'], key)
    return ctx


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path, help='delivery-api checkout (passed by req-owner.sh)')
    p.add_argument('--apply', action='store_true', help='run the sequence; default is the dry plan review')
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        ctx = build_ctx(args.root.resolve())
        if not args.apply:
            plan(ctx)
            print('DRY REVIEW ONLY: rerun with --apply to run the sequence; every existing typed confirmation stays.')
            return 0
        apply(ctx)
        return 0
    except WorkflowError as error:
        log('FAIL', str(error))
        log('INFO', 'No automatic retry or alternate path. Fix the named input and rerun the same command; completed steps are revalidated and skipped.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
