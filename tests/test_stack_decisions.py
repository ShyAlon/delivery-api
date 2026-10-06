import json

from tests.test_decision_pages import setup

REV = 'a' * 64


def _arch_setup(tmp_path, monkeypatch, slots=2):
    from vmodel_contracts.runtime import artifacts_root
    import delivery_api.app as module
    store, tid, engine, c = setup(tmp_path, monkeypatch)
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR', str(tmp_path / 'artifacts'))
    store.wait_at_gate(tid, 'architecture')
    spec = artifacts_root(str(tmp_path / 'artifacts')) / 'session-one' / 'spec'
    spec.mkdir(parents=True)
    names = ['database', 'queue'][:slots]
    artifact = spec / 'architecture.json'
    artifact.write_text(json.dumps({'stack_entry_dispositions': [
        {'key': n, 'status': 'research_required', 'research_brief': f'Which {n}?'} for n in names]}))
    artifact.with_suffix('.md').write_text('STALE')
    (spec / 'architecture.decisions.json').write_text(json.dumps({'revision': REV, 'slots': [
        {'key': n, 'brief': f'Which {n}?', 'options': ['A', 'B'], 'founder_decision': None, 'prefill': None} for n in names]}))
    monkeypatch.setattr(module, '_task_artifact_snapshot', lambda task: [
        {'phase_key': 'architecture', 'name': 'architecture.json', 'ref': str(artifact), 'present': True,
         'valid': True, 'stale': False, 'generation': 1}])
    return store, tid, c, spec, artifact


def test_review_carries_open_slots_and_approve_is_refused_until_all_decided(tmp_path, monkeypatch):
    store, tid, c, spec, artifact = _arch_setup(tmp_path, monkeypatch)
    review = c.get(f'/api/tasks/{tid}/gates/gate-architecture/review').json()['review']
    assert [s['key'] for s in review['stack_decisions']['slots']] == ['database', 'queue']
    denied = c.post(f'/api/tasks/{tid}/gates/gate-architecture/decide', json={'action': 'approve', 'selected_option': 'Proceed'})
    assert denied.status_code == 400 and '2 architecture decision' in json.dumps(denied.json())
    part = c.post(f'/api/tasks/{tid}/gates/gate-architecture/stack-decisions',
                  json={'revision': REV, 'decisions': [{'key': 'database', 'text': 'SQLite', 'reason': 'One user'}]})
    assert part.status_code == 200 and part.json()['open'] == ['queue']
    denied = c.post(f'/api/tasks/{tid}/gates/gate-architecture/decide', json={'action': 'approve', 'selected_option': 'Proceed'})
    assert denied.status_code == 400 and '1 architecture decision' in json.dumps(denied.json())
    stored = json.loads(artifact.read_text())['stack_entry_dispositions']
    assert stored[0]['founder_decision']['text'] == 'SQLite' and stored[0]['founder_decision']['actor'] == 'local user'
    assert 'founder_decision' not in stored[1]
    assert not artifact.with_suffix('.md').exists()
    companion = json.loads((spec / 'architecture.decisions.json').read_text())
    assert companion['slots'][0]['founder_decision']['reason'] == 'One user'


def test_blank_unknown_and_stale_are_refused(tmp_path, monkeypatch):
    store, tid, c, spec, artifact = _arch_setup(tmp_path, monkeypatch, slots=1)
    url = f'/api/tasks/{tid}/gates/gate-architecture/stack-decisions'
    for bad in ({'key': 'database', 'text': ' ', 'reason': 'r'}, {'key': 'database', 'text': 'x', 'reason': ''},
                {'key': 'nope', 'text': 'x', 'reason': 'r'}):
        assert c.post(url, json={'revision': REV, 'decisions': [bad]}).status_code == 422
    assert c.post(url, json={'revision': 'b' * 64, 'decisions': [{'key': 'database', 'text': 'x', 'reason': 'r'}]}).status_code == 409
    assert 'founder_decision' not in json.loads(artifact.read_text())['stack_entry_dispositions'][0]


def test_no_open_slot_file_means_no_panel_and_approval_unchanged(tmp_path, monkeypatch):
    store, tid, c, spec, artifact = _arch_setup(tmp_path, monkeypatch, slots=1)
    (spec / 'architecture.decisions.json').unlink()
    review = c.get(f'/api/tasks/{tid}/gates/gate-architecture/review').json()['review']
    assert review['stack_decisions'] is None
    reply = c.post(f'/api/tasks/{tid}/gates/gate-architecture/decide', json={'action': 'approve', 'selected_option': 'Proceed'})
    assert 'architecture decision' not in json.dumps(reply.json())
