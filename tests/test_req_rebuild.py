"""req rebuild orchestrator: resume, owner gates and cleanup selection.

Fail-first on main: scripts/owner/rebuild.py does not exist and neither req
nor req-owner.sh mentions rebuild, so every test here is red.
"""
import datetime
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('owner_rebuild', ROOT / 'scripts/owner/rebuild.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SHAS = {name: ('%x' % (i + 1)) * 40 for i, name in enumerate(m.REPOS)}
IMAGE_ID = 'sha256:' + 'ab' * 32


def ctx(tmp_path, **over):
    base = {'root': tmp_path, 'repos_root': tmp_path, 'req': tmp_path / 'req',
            'home': tmp_path, 'shas': dict(SHAS),
            'runtime_contract': 'sha256:rt', 'acceptance_contract': 'sha256:ac',
            'state_path': tmp_path / 'state.json',
            'state': {'schema': 1, 'key': 'k', 'steps': {}},
            'typed': lambda prompt: '', 'run_child': lambda args, **kw: None,
            'docker': lambda a, **kw: '', 'image_exists': lambda i: True,
            'heads': lambda: dict(SHAS), 'now': datetime.datetime.now}
    base.update(over)
    return base


def test_repos_match_pull_all_script():
    text = (ROOT / 'scripts/pull-all.sh').read_text()
    line = next(l for l in text.splitlines() if l.startswith('REPOS='))
    assert set(line.split('"')[1].split()) == set(m.REPOS)


def test_req_front_end_and_dispatcher_wire_rebuild():
    req = (ROOT / 'scripts/ops/req').read_text()
    owner = (ROOT / 'scripts/ops/req-owner.sh').read_text()
    assert 'locks|rebuild)' in req, 'req must route rebuild through the clean-main sync'
    assert 'rebuild) target=scripts/owner/rebuild.py;;' in owner
    assert 'scripts/owner/rebuild.py' in owner and '"$PWD"' in owner


def test_load_state_with_changed_key_starts_fresh(tmp_path):
    path = tmp_path / 'state.json'
    path.write_text(json.dumps({'schema': 1, 'key': 'old', 'steps': {'pull-all': {'completed_at': 'x', 'evidence': {}}}}))
    assert m.load_state(path, 'new') == {'schema': 1, 'key': 'new', 'steps': {}}
    assert m.load_state(path, 'old')['steps'].get('pull-all'), 'same key keeps completed steps'


def test_pull_all_completed_and_verified_is_not_rerun(tmp_path):
    calls = []
    state = {'schema': 1, 'key': 'k', 'steps': {'pull-all': {'completed_at': 'x', 'evidence': {'heads': dict(SHAS)}}}}
    m.step_pull_all(ctx(tmp_path, state=state, run_child=lambda a, **k: calls.append(a)))
    assert calls == [], 'a recorded pull-all with matching heads must be skipped'


def test_pull_all_rerun_when_a_checkout_moved(tmp_path):
    calls = []
    moved = dict(SHAS); moved['spec-generator'] = 'f' * 40
    state = {'schema': 1, 'key': 'k', 'steps': {'pull-all': {'completed_at': 'x', 'evidence': {}}}}
    c = ctx(tmp_path, state=state, run_child=lambda a, **k: calls.append(a), heads=lambda: moved)
    with pytest.raises(m.WorkflowError, match='spec-generator'):
        m.step_pull_all(c)
    assert calls and 'pull-all.sh' in ' '.join(calls[0])


def test_migrate_apply_is_the_owners_gate_and_records(tmp_path):
    calls = []
    answers = iter(['APPLY MIGRATIONS'])
    c = ctx(tmp_path, run_child=lambda a, **k: calls.append(list(a)), typed=lambda p: next(answers))
    m.step_migrate(c)
    flat = [' '.join(map(str, a)) for a in calls]
    assert flat[0].endswith('req migrate'), 'the dry review always runs first'
    assert flat[1].endswith('req migrate --apply')
    assert 'migrate-apply' in c['state']['steps']


def test_migrate_skip_continues_without_applying(tmp_path):
    calls = []
    c = ctx(tmp_path, run_child=lambda a, **k: calls.append(a), typed=lambda p: 'SKIP')
    m.step_migrate(c)
    assert len(calls) == 1 and '--apply' not in ' '.join(map(str, calls[0]))
    assert 'migrate-apply' not in c['state']['steps']


def test_migrate_any_other_answer_stops_the_sequence(tmp_path):
    calls = []
    c = ctx(tmp_path, run_child=lambda a, **k: calls.append(a), typed=lambda p: 'yes')
    with pytest.raises(m.WorkflowError, match='declined'):
        m.step_migrate(c)
    assert len(calls) == 1, 'req migrate --apply must not run without the exact typed string'


def test_children_never_receive_answered_stdin(tmp_path):
    import inspect
    source = inspect.getsource(m.run_child)
    assert 'stdin=' not in source and 'input=' not in source and '.communicate' not in source, \
        'run_child must inherit the terminal, never feed a confirmation'


def test_cleanup_keeps_running_and_previous(tmp_path):
    containers = [
        {'Names': 'requi-preview-exec', 'State': 'running', 'Image': IMAGE_ID, 'ID': 'cur', 'CreatedAt': '2026-11-08'},
        {'Names': 'requi-preview-exec-20261107-090000', 'State': 'exited', 'Image': 'sha256:prev', 'ID': 'prev', 'CreatedAt': '2026-11-07'},
        {'Names': 'requi-preview-exec-20261101-090000', 'State': 'exited', 'Image': 'sha256:old', 'ID': 'old', 'CreatedAt': '2026-11-01'},
    ]
    images = [{'Repository': 'requi-full-candidate', 'Tag': 'new', 'ID': IMAGE_ID},
              {'Repository': 'requi-full-candidate', 'Tag': 'old', 'ID': 'sha256:old'},
              {'Repository': '<none>', 'Tag': '<none>', 'ID': 'sha256:dangle'}]
    kept, removable, old_images = m.plan_cleanup(containers, images, keep_previous=1, keep_image_ids={IMAGE_ID, 'sha256:prev'})
    assert [k['Names'] for k in kept] == ['requi-preview-exec-20261107-090000'], 'newest renamed container is the kept previous'
    assert [r['Names'] for r in removable] == ['requi-preview-exec-20261101-090000']
    assert sorted(i['ID'] for i in old_images) == ['sha256:dangle', 'sha256:old'], 'kept images are never listed'


def test_cleanup_declined_removes_nothing(tmp_path):
    removed = []
    containers = [{'Names': 'requi-preview-exec-20261107-090000', 'State': 'exited', 'Image': 'sha256:prev', 'ID': 'prev', 'CreatedAt': '2026-11-07'},
                  {'Names': 'requi-preview-exec-20261101-090000', 'State': 'exited', 'Image': 'sha256:old', 'ID': 'old', 'CreatedAt': '2026-11-01'}]

    def docker(args, **kw):
        if args[0] in ('rm', 'rmi'):
            removed.append(args)
        if args[0] == 'ps' and '-q' in args:
            return '\n'.join(c['ID'] for c in containers)
        if args[0] == 'ps':
            return json.dumps(containers)
        if args[0] == 'images':
            return json.dumps([])
        if args[:2] == ['container', 'inspect'] and args[3] == '{{.Image}}':
            return ''
        if args[0] == 'container':
            raise m.WorkflowError('no such container')
        return ''

    c = ctx(tmp_path, docker=docker, typed=lambda p: 'DELETE EVERYTHING')
    m.step_cleanup(c)
    assert removed == [], 'anything but the exact quoted string keeps every item'
    assert 'cleanup' not in c['state']['steps']


def test_run_child_keyboard_interrupt_counts_only_when_interruptible():
    class Proc:
        def __init__(self):
            self.waits = 0
        def wait(self):
            self.waits += 1
            if self.waits == 1:
                raise KeyboardInterrupt
            return 0
    m.run_child(['x'], interruptible=True, popen=lambda a: Proc())
    with pytest.raises(m.WorkflowError, match='interrupted'):
        m.run_child(['x'], interruptible=False, popen=lambda a: Proc())


def test_preflight_names_the_missing_tool(tmp_path, monkeypatch):
    root = tmp_path / 'delivery-api'
    (root / '.git').mkdir(parents=True)
    (root / '.venv/bin').mkdir(parents=True)
    (root / '.venv/bin/python').write_text('')
    tty = tmp_path / 'tty'
    tty.write_text('')
    monkeypatch.setattr(m, 'venv_check', lambda r: None)
    with pytest.raises(m.WorkflowError, match='docker'):
        m.preflight(root, tmp_path, which=lambda t: None if t == 'docker' else '/usr/bin/' + t,
                    run=lambda *a, **k: '', tty_path=str(tty))


def test_accepted_receipt_matches_only_current_sources(tmp_path):
    base = tmp_path / '.local/share/requi/image-workflow'
    for run_name, sources in (('matching', {n: SHAS[n] for n in m.IMAGE_REPOS}), ('stale', {n: '0' * 40 for n in m.IMAGE_REPOS})):
        run = base / run_name
        run.mkdir(parents=True)
        (run / 'state.json').write_text(json.dumps({'stages': {'local-acceptance': {
            'image_id': IMAGE_ID, 'runtime_contract': 'sha256:rt',
            'checks': {name: 'passed' for name in m.ACCEPTANCE_CHECKS}}}}))
        (run / 'accepted-image.json').write_text(json.dumps({
            'config_digest': IMAGE_ID, 'sources': sources,
            'runtime_contract': 'sha256:rt', 'acceptance_contract': 'sha256:ac'}))
    found = m.accepted_receipt(ctx(tmp_path))
    assert found and found['image_id'] == IMAGE_ID
    missing = m.accepted_receipt(ctx(tmp_path, shas={n: '9' * 40 for n in m.REPOS}))
    assert missing is None, 'a receipt for older sources must not be reused'


def _stage_run(tmp_path, checks, input_key=None, build_sources=None):
    """A req image --accept-local run dir exactly where the inputs digest puts it."""
    inputs = {'sources': build_sources or {n: SHAS[n] for n in m.IMAGE_REPOS},
              'platform': 'linux/amd64', 'runtime_contract': 'sha256:rt',
              'acceptance_contract': 'sha256:ac'}
    name = m.digest(json.dumps(inputs, sort_keys=True).encode()).split(':')[1][:24]
    key = input_key if input_key is not None else m.digest(
        json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode())
    run = tmp_path / '.local/share/requi/image-workflow' / name
    run.mkdir(parents=True)
    (run / 'state.json').write_text(json.dumps({'schema': 1, 'input_key': key, 'stages': {
        'build': {'image_id': IMAGE_ID, 'sources': inputs['sources'], 'platform': 'linux/amd64'},
        'local-acceptance': {'image_id': IMAGE_ID, 'runtime_contract': 'sha256:rt', 'checks': checks}}}))
    return run


def test_local_stage_evidence_verifies_without_a_publish_receipt(tmp_path):
    run = _stage_run(tmp_path, {name: 'passed' for name in m.ACCEPTANCE_CHECKS})
    assert not (run / 'accepted-image.json').exists(), 'accept-local never writes the publish receipt'
    found = m.accepted_receipt(ctx(tmp_path))
    assert found and found['image_id'] == IMAGE_ID and found['run_dir'] == str(run)


def test_stage_evidence_with_a_failed_acceptance_is_rejected(tmp_path):
    checks = {name: 'passed' for name in m.ACCEPTANCE_CHECKS}
    checks['stop'] = 'failed'
    _stage_run(tmp_path, checks)
    assert m.accepted_receipt(ctx(tmp_path)) is None


def test_stage_evidence_is_bound_to_the_exact_contracts(tmp_path):
    _stage_run(tmp_path, {name: 'passed' for name in m.ACCEPTANCE_CHECKS}, input_key='sha256:forged')
    assert m.accepted_receipt(ctx(tmp_path)) is None
    _stage_run(tmp_path, {name: 'passed' for name in m.ACCEPTANCE_CHECKS},
               build_sources={n: '0' * 40 for n in m.IMAGE_REPOS})
    assert m.accepted_receipt(ctx(tmp_path)) is None, 'evidence for other sources lives at another digest and must not match'
def test_pull_all_verification_reads_checkout_heads_not_origin_refs(tmp_path, monkeypatch):
    """build_ctx wires heads to rev-parse HEAD; origin refs alone proved nothing."""
    monkeypatch.setenv('REQUI_REBUILD_STATE', str(tmp_path / 'rebuild-state.json'))
    monkeypatch.setattr(m, 'preflight', lambda root, repos_root: dict(SHAS))
    monkeypatch.setattr(m, 'contract_bytes', lambda: b'contract')
    monkeypatch.setattr(m, 'acceptance_contract', lambda root: 'sha256:ac')
    monkeypatch.setattr(m, 'origin_shas', lambda *a, **k: {n: '9' * 40 for n in m.REPOS})
    stale = dict(SHAS)
    stale['vmodel-contracts'] = '0' * 40
    def fake_command(args, **kw):
        return stale[Path(args[args.index('-C') + 1]).name]
    monkeypatch.setattr(m, 'command', fake_command)
    built = m.build_ctx(tmp_path / 'delivery-api')
    assert built['heads']() == stale, 'heads must come from each checkout, not from origin refs'


def test_pull_all_names_the_checkout_left_behind(tmp_path):
    moved = dict(SHAS)
    moved['vmodel-spec-app'] = '0' * 40
    with pytest.raises(m.WorkflowError, match='vmodel-spec-app'):
        m.step_pull_all(ctx(tmp_path, heads=lambda: moved))


# --- 2026-11-10: cleanup compared full image ids with `docker images` short ids ---
FULL = 'sha256:' + '8fbb4befdb29' + 'c0' * 26   # what `docker container inspect` returns
SHORT = '8fbb4befdb29'                           # what `docker images` prints


def test_cleanup_keep_list_matches_short_and_full_image_ids():
    containers = [{'Names': 'requi-preview-exec', 'State': 'running', 'Image': 'x', 'ID': 'cur', 'CreatedAt': '2026-11-10'}]
    images = [{'Repository': 'requi-full-candidate', 'Tag': 'new', 'ID': SHORT},
              {'Repository': 'requi-full-candidate', 'Tag': 'old', 'ID': 'aaaaaaaaaaaa'}]
    _, _, old = m.plan_cleanup(containers, images, keep_previous=1, keep_image_ids={FULL})
    assert [i['ID'] for i in old] == ['aaaaaaaaaaaa'], \
        'the running preview image (full id) must be protected from its short-id listing'


def _cleanup_docker(containers, images, in_use, fail=None, calls=None):
    def docker(args, **kw):
        if args[0] == 'ps' and '-q' in args:
            return '\n'.join(c['ID'] for c in containers)
        if args[0] == 'ps':
            return json.dumps(containers)
        if args[0] == 'images':
            return json.dumps(images)
        if args[:2] == ['container', 'inspect']:
            target = args[-1]
            if args[3] == '{{.Image}}':
                return '\n'.join(in_use[t] for t in args[4:] if t in in_use)
            if target == 'requi-preview-exec':
                return json.dumps({'Image': FULL, 'State': {'Running': True}})
            if target in in_use:
                return json.dumps({'Image': in_use[target], 'State': {'Running': False}})
            raise m.WorkflowError('no such container')
        return ''

    def docker_try(args, **kw):
        if calls is not None:
            calls.append(args)
        if fail and args[-1] in fail:
            return 1, fail[args[-1]]
        return 0, ''
    return docker, docker_try


def test_cleanup_reports_item_and_docker_reason_and_continues(tmp_path):
    containers = [{'Names': 'requi-preview-exec-20261101-090000', 'State': 'exited', 'Image': 'o', 'ID': 'old1', 'CreatedAt': '2026-11-01'},
                  {'Names': 'requi-preview-exec-20261107-090000', 'State': 'exited', 'Image': 'p', 'ID': 'prev', 'CreatedAt': '2026-11-07'}]
    images = [{'Repository': 'requi-full-candidate', 'Tag': 't1', 'ID': 'bbbbbbbbbbbb'},
              {'Repository': 'requi-full-candidate', 'Tag': 't2', 'ID': 'cccccccccccc'}]
    calls = []
    docker, docker_try = _cleanup_docker(containers, images, {}, fail={'bbbbbbbbbbbb': 'Error response from daemon: conflict: image is being used by stopped container 1a2b'}, calls=calls)
    c = ctx(tmp_path, docker=docker, docker_try=docker_try, typed=lambda p: 'DELETE 1 CONTAINERS AND 2 IMAGES')
    try:
        m.step_cleanup(c)
    except m.WorkflowError as error:
        text = str(error)
    else:
        raise AssertionError('a failed removal must fail the step')
    assert 'docker rmi bbbbbbbbbbbb' in text and 'requi-full-candidate:t1' in text, text
    assert 'conflict: image is being used by stopped container 1a2b' in text, 'docker reason must be printed'
    assert ['rmi', 'cccccccccccc'] in calls, 'one failing item must not stop the remaining removals'
    assert 'cleanup' not in c['state']['steps']


def test_cleanup_never_lists_image_used_by_a_surviving_container(tmp_path):
    containers = [{'Names': 'requi-preview-exec-20261107-090000', 'State': 'exited', 'Image': 'p', 'ID': 'prev', 'CreatedAt': '2026-11-07'}]
    images = [{'Repository': 'requi-full-candidate', 'Tag': 'p', 'ID': 'dddddddddddd'},
              {'Repository': '<none>', 'Tag': '<none>', 'ID': 'eeeeeeeeeeee'}]
    calls = []
    docker, docker_try = _cleanup_docker(containers, images, {'prev': 'sha256:' + 'dddddddddddd' + '00' * 26}, calls=calls)
    c = ctx(tmp_path, docker=docker, docker_try=docker_try, typed=lambda p: 'DELETE 0 CONTAINERS AND 1 IMAGES')
    m.step_cleanup(c)
    assert calls == [['rmi', 'eeeeeeeeeeee']], calls
    assert 'cleanup' in c['state']['steps']
