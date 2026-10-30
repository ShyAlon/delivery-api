import json

from tests.test_decision_pages import setup

REV = 'a' * 64


class _PGSpec:
    def __init__(self,store,tid,phase='architecture'):self.store=store;self.tid=tid;self.phase=phase
    def __truediv__(self,name):return _PGArtifact(self.store,self.tid,name,self.phase)

class _PGArtifact:
    def __init__(self,store,tid,name,phase='architecture'):self.store=store;self.tid=tid;self.name=name;self.phase=phase
    def read_bytes(self):
        if self.name=='lineage.json':
            from delivery_api.generator_documents import GeneratorDocuments
            return GeneratorDocuments.owner_reader(self.store,self.tid).read('spec/'+self.name)
        return self.store.read_artifact_bytes(self.tid,self.phase,self.name)
    def read_text(self):return self.read_bytes().decode()
    def write_text(self,text):
        from delivery_api.artifact_publication import publish_preserving_revision
        if self.name.endswith('.md') or self.name=='lineage.json':
            from delivery_api.generator_documents import GeneratorDocuments
            from delivery_api.worker_capability import WorkerCapability
            with self.store._task_operation_guard('task.gate.decide'):
                reader=GeneratorDocuments.reader(self.store,WorkerCapability.capture(self.store,self.tid))
                import hashlib
                reader._put('spec/'+self.name,text.encode(),hashlib.sha256(text.encode()).hexdigest())
        else:
            # Seed/rewrite full manifest without weakening runtime gate writer checks.
            with self.store._task_operation_guard('task.gate.decide'):
                publish_preserving_revision(self.store,self.tid,{(self.phase,self.name):text.encode()})
    def unlink(self):
        from delivery_api.artifact_publication import publish_preserving_revision
        with self.store._task_operation_guard('task.gate.decide'):
            snapshot=self.store.manifest_snapshot(self.tid)
            artifacts=[{'phase_key':row['phase_key'],'name':row['name'],'data':self.store.read_artifact_bytes_sha(row['sha256'])} for row in snapshot['rows'] if (row['phase_key'],row['name'])!=(self.phase,self.name)]
            self.store.publish_task_revision_bytes(self.tid,artifacts,writer_instance='test-only')
    def with_suffix(self,suffix):return _PGArtifact(self.store,self.tid,self.name.rsplit('.',1)[0]+suffix,self.phase)
    def exists(self):
        from delivery_api.generator_documents import GeneratorDocuments
        return GeneratorDocuments.owner_reader(self.store,self.tid).read('spec/'+self.name) is not None


def _arch_setup(tmp_path, monkeypatch,pg_scoped_workspace, slots=2):
    from vmodel_contracts.runtime import artifacts_root
    import delivery_api.app as module
    store, tid, engine, c = setup(tmp_path, monkeypatch,pg_scoped_workspace)
    sid=engine.session_id
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR', str(tmp_path / 'artifacts'))
    store.wait_at_gate(tid, 'architecture')
    # Artifact-like test handle backed by exact PG bytes, not filesystem fixtures.
    spec = _PGSpec(store,tid)
    names = ['database', 'queue'][:slots]
    artifact = spec / 'architecture.json'
    artifact.write_text(json.dumps({'stack_entry_dispositions': [
        {'key': n, 'status': 'research_required', 'research_brief': f'Which {n}?'} for n in names]}))
    artifact.with_suffix('.md').write_text('STALE')
    (spec / 'architecture.decisions.json').write_text(json.dumps({'revision': REV, 'slots': [
        {'key': n, 'brief': f'Which {n}?', 'options': ['A', 'B'], 'founder_decision': None, 'prefill': None} for n in names]}))
    store.update_phase(tid,'architecture','awaiting_approval')
    monkeypatch.setattr(module, '_task_artifact_snapshot', lambda snapshot_task_id, **kwargs: [
        {'phase_key': 'architecture', 'name': 'architecture.json', 'ref': str(artifact), 'present': True,
         'valid': True, 'stale': False, 'generation': 1}])
    return store, tid, c, spec, artifact


def _case_review_carries_open_slots_and_approve_is_refused_until_all_decided(tmp_path, monkeypatch,pg_scoped_workspace):
    store, tid, c, spec, artifact = _arch_setup(tmp_path, monkeypatch,pg_scoped_workspace)
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


def _case_blank_unknown_and_stale_are_refused(tmp_path, monkeypatch,pg_scoped_workspace):
    store, tid, c, spec, artifact = _arch_setup(tmp_path, monkeypatch,pg_scoped_workspace, slots=1)
    url = f'/api/tasks/{tid}/gates/gate-architecture/stack-decisions'
    for bad in ({'key': 'database', 'text': ' ', 'reason': 'r'}, {'key': 'nope', 'text': 'x', 'reason': 'r'}):
        assert c.post(url, json={'revision': REV, 'decisions': [bad]}).status_code == 422
    assert c.post(url, json={'revision': 'b' * 64, 'decisions': [{'key': 'database', 'text': 'x', 'reason': 'r'}]}).status_code == 409
    assert 'founder_decision' not in json.loads(artifact.read_text())['stack_entry_dispositions'][0]


def _case_missing_companion_with_research_required_slots_fails_closed(tmp_path, monkeypatch,pg_scoped_workspace):
    # Integrity: research_required slots mean the generator wrote the founder-decision
    # companion. A missing companion (e.g. fresh host before restore/backfill) must
    # REFUSE approval with a recoverable missing-evidence error - never silently
    # approve without the required evidence.
    store, tid, c, spec, artifact = _arch_setup(tmp_path, monkeypatch,pg_scoped_workspace, slots=1)
    sid=store.task(tid)['engine_session_id']
    (spec / 'architecture.decisions.json').unlink()
    review = c.get(f'/api/tasks/{tid}/gates/gate-architecture/review').json()['review']
    assert review['stack_decisions'] is None
    denied = c.post(f'/api/tasks/{tid}/gates/gate-architecture/decide', json={'action': 'approve', 'selected_option': 'Proceed'})
    assert denied.status_code == 400 and 'Required approval evidence is missing' in denied.text, denied.text
    # Recovery: the companion lands (restore/backfill), the slot gets decided, approval proceeds.
    store.set_model_default(tid, "deepseek:deepseek-flash")
    async def no_continue(*args, **kwargs): pass
    monkeypatch.setattr(__import__('delivery_api.app', fromlist=['orchestrator']).orchestrator, 'continue_after', no_continue)
    (spec / 'architecture.decisions.json').write_text(json.dumps({'revision': REV, 'slots': [
        {'key': 'database', 'brief': 'Which database?', 'founder_decision': {'text': 'SQLite', 'reason': 'One user', 'actor': 'local user', 'at': '2026-10-24T00:00:00+00:00'}}]}))
    decided = {'text': 'SQLite', 'reason': 'One user', 'actor': 'local user', 'at': '2026-10-24T00:00:00+00:00'}
    from vmodel_contracts.architecture import ArchitectureOverview
    full = {'session_id': sid, 'stack_entries': [], 'components': [], 'decisions': [], 'non_functionals': {},
            'stack_entry_dispositions': [{'key': 'database', 'status': 'research_required', 'research_brief': 'Which database?',
                                          'founder_decision': decided}]}
    ArchitectureOverview.model_validate(full)
    artifact.write_text(json.dumps(full))
    reply = c.post(f'/api/tasks/{tid}/gates/gate-architecture/decide', json={'action': 'approve', 'selected_option': 'Proceed'})
    assert reply.status_code == 200, reply.text


def _case_no_research_required_slots_need_no_companion(tmp_path, monkeypatch,pg_scoped_workspace):
    # Genuinely non-applicable: no research_required dispositions, so no companion
    # was ever produced - approval proceeds without it.
    store, tid, c, spec, artifact = _arch_setup(tmp_path, monkeypatch,pg_scoped_workspace, slots=1)
    artifact.write_text(json.dumps({'stack_entry_dispositions': [{'key': 'database', 'status': 'resolved', 'value': 'SQLite'}]}))
    (spec / 'architecture.decisions.json').unlink()
    store.set_model_default(tid, "deepseek:deepseek-flash")
    async def no_continue(*args, **kwargs): pass
    monkeypatch.setattr(__import__('delivery_api.app', fromlist=['orchestrator']).orchestrator, 'continue_after', no_continue)
    review = c.get(f'/api/tasks/{tid}/gates/gate-architecture/review').json()['review']
    assert review['stack_decisions'] is None
    reply = c.post(f'/api/tasks/{tid}/gates/gate-architecture/decide', json={'action': 'approve', 'selected_option': 'Proceed'})
    assert reply.status_code == 200, reply.text


FRAMING = {"title": "Storing Your Data", "purpose": "Keeps the product's records between visits.", "default_value": "SQLite", "reason": "A single local user needs a small store.", "requirement_ids": ["R-1"], "options": [{"value": "SQLite", "label": "One Local Store", "implication": "Simple to run; a shared server may be needed later."}, {"value": "PostgreSQL", "label": "A Shared Store", "implication": "Supports shared access but adds a server to manage."}], "provenance": "design_proposal", "research_status": "pending"}

def _with_defaults(spec, artifact):
    from vmodel_contracts.architecture import ArchitectureOverview
    full={"session_id":"session-one", "stack_entries":[], "stack_entry_dispositions":[{"key":"database", "status":"research_required", "research_brief":"INTERNAL INVESTIGATION DO NOT SHOW", "technical_choice":FRAMING}], "components":[], "decisions":[], "non_functionals":{}}
    ArchitectureOverview.model_validate(full)
    artifact.write_text(json.dumps(full))
    (spec/'architecture.decisions.json').write_text(json.dumps({"revision":REV,"slots":[{"key":"database","brief":"internal", "technical_choice":FRAMING,"founder_decision":None}]}))


def _case_approval_uses_displayed_default_without_founder_answer(tmp_path,monkeypatch,pg_scoped_workspace):
    store,tid,c,spec,artifact=_arch_setup(tmp_path,monkeypatch,pg_scoped_workspace,slots=1)
    _with_defaults(spec,artifact)
    store.set_model_default(tid,"deepseek:deepseek-flash")
    async def no_continue(*args,**kwargs): pass
    monkeypatch.setattr(__import__('delivery_api.app',fromlist=['orchestrator']).orchestrator,'continue_after',no_continue)
    reply=c.post(f'/api/tasks/{tid}/gates/gate-architecture/decide',json={'action':'approve','selected_option':'Proceed'})
    assert reply.status_code==200,reply.text
    entry=json.loads(artifact.read_text())['stack_entry_dispositions'][0]
    assert entry['system_selection']=={'value':'SQLite','source':'architecture_default'}
    assert not entry.get('founder_decision')
    assert json.loads((spec/'architecture.decisions.json').read_text())['slots'][0]['system_selection']==entry['system_selection']


def _case_override_reason_optional_and_not_replaced_by_design_reason(tmp_path,monkeypatch,pg_scoped_workspace):
    store,tid,c,spec,artifact=_arch_setup(tmp_path,monkeypatch,pg_scoped_workspace,slots=1);_with_defaults(spec,artifact)
    reply=c.post(f'/api/tasks/{tid}/gates/gate-architecture/stack-decisions',json={'revision':REV,'decisions':[{'key':'database','text':'Custom'}]})
    assert reply.status_code==200,reply.text
    entry=json.loads(artifact.read_text())['stack_entry_dispositions'][0]
    assert entry['founder_decision']['reason']==''
    from delivery_api.app import _use_architecture_defaults
    _use_architecture_defaults(tid)
    entry=json.loads(artifact.read_text())['stack_entry_dispositions'][0]
    assert entry['founder_decision']['text']=='Custom' and not entry.get('system_selection')


def _case_invalid_artifact_or_mismatched_framing_is_not_approved(tmp_path,monkeypatch,pg_scoped_workspace):
    store,tid,c,spec,artifact=_arch_setup(tmp_path,monkeypatch,pg_scoped_workspace,slots=1);_with_defaults(spec,artifact)
    data=json.loads(artifact.read_text());data['stack_entry_dispositions'][0]['technical_choice']['default_value']='Redis';artifact.write_text(json.dumps(data))
    reply=c.post(f'/api/tasks/{tid}/gates/gate-architecture/decide',json={'action':'approve','selected_option':'Proceed'})
    assert reply.status_code==400


def _case_stale_review_refuses_before_default_write(tmp_path,monkeypatch,pg_scoped_workspace):
    store,tid,c,spec,artifact=_arch_setup(tmp_path,monkeypatch,pg_scoped_workspace,slots=1);_with_defaults(spec,artifact)
    before=artifact.read_bytes()
    reply=c.post(f'/api/tasks/{tid}/gates/gate-architecture/decide',json={'action':'approve','review_revision':'stale'})
    assert reply.status_code==409 and artifact.read_bytes()==before


def _case_companion_mismatch_cannot_supply_a_default(tmp_path,monkeypatch,pg_scoped_workspace):
    store,tid,c,spec,artifact=_arch_setup(tmp_path,monkeypatch,pg_scoped_workspace,slots=1);_with_defaults(spec,artifact)
    path=spec/'architecture.decisions.json';data=json.loads(path.read_text());data['slots'][0]['technical_choice']['reason']='different';path.write_text(json.dumps(data))
    from delivery_api.app import _use_architecture_defaults
    import pytest
    with pytest.raises(ValueError,match='match'): _use_architecture_defaults(tid)
    assert not json.loads(artifact.read_text())['stack_entry_dispositions'][0].get('system_selection')


def _check_review_carries_open_slots_and_approve_is_refused_until_all_decided(monkeypatch,tmp_path,workspace):
 from tests.test_stack_decisions import _case_review_carries_open_slots_and_approve_is_refused_until_all_decided
 _case_review_carries_open_slots_and_approve_is_refused_until_all_decided(tmp_path,monkeypatch,workspace)


def test_review_carries_open_slots_and_approve_is_refused_until_all_decided(pg_local_compat_process):
 pg_local_compat_process(_check_review_carries_open_slots_and_approve_is_refused_until_all_decided)


def _check_blank_unknown_and_stale_are_refused(monkeypatch,tmp_path,workspace):
 from tests.test_stack_decisions import _case_blank_unknown_and_stale_are_refused
 _case_blank_unknown_and_stale_are_refused(tmp_path,monkeypatch,workspace)


def test_blank_unknown_and_stale_are_refused(pg_local_compat_process):
 pg_local_compat_process(_check_blank_unknown_and_stale_are_refused)


def _check_missing_companion_with_research_required_slots_fails_closed(monkeypatch,tmp_path,workspace):
 from tests.test_stack_decisions import _case_missing_companion_with_research_required_slots_fails_closed
 _case_missing_companion_with_research_required_slots_fails_closed(tmp_path,monkeypatch,workspace)


def test_missing_companion_with_research_required_slots_fails_closed(pg_local_compat_process):
 pg_local_compat_process(_check_missing_companion_with_research_required_slots_fails_closed)


def _check_no_research_required_slots_need_no_companion(monkeypatch,tmp_path,workspace):
 from tests.test_stack_decisions import _case_no_research_required_slots_need_no_companion
 _case_no_research_required_slots_need_no_companion(tmp_path,monkeypatch,workspace)


def test_no_research_required_slots_need_no_companion(pg_local_compat_process):
 pg_local_compat_process(_check_no_research_required_slots_need_no_companion)


def _check_approval_uses_displayed_default_without_founder_answer(monkeypatch,tmp_path,workspace):
 from tests.test_stack_decisions import _case_approval_uses_displayed_default_without_founder_answer
 _case_approval_uses_displayed_default_without_founder_answer(tmp_path,monkeypatch,workspace)


def test_approval_uses_displayed_default_without_founder_answer(pg_local_compat_process):
 pg_local_compat_process(_check_approval_uses_displayed_default_without_founder_answer)


def _check_override_reason_optional_and_not_replaced_by_design_reason(monkeypatch,tmp_path,workspace):
 from tests.test_stack_decisions import _case_override_reason_optional_and_not_replaced_by_design_reason
 _case_override_reason_optional_and_not_replaced_by_design_reason(tmp_path,monkeypatch,workspace)


def test_override_reason_optional_and_not_replaced_by_design_reason(pg_local_compat_process):
 pg_local_compat_process(_check_override_reason_optional_and_not_replaced_by_design_reason)


def _check_invalid_artifact_or_mismatched_framing_is_not_approved(monkeypatch,tmp_path,workspace):
 from tests.test_stack_decisions import _case_invalid_artifact_or_mismatched_framing_is_not_approved
 _case_invalid_artifact_or_mismatched_framing_is_not_approved(tmp_path,monkeypatch,workspace)


def test_invalid_artifact_or_mismatched_framing_is_not_approved(pg_local_compat_process):
 pg_local_compat_process(_check_invalid_artifact_or_mismatched_framing_is_not_approved)


def _check_stale_review_refuses_before_default_write(monkeypatch,tmp_path,workspace):
 from tests.test_stack_decisions import _case_stale_review_refuses_before_default_write
 _case_stale_review_refuses_before_default_write(tmp_path,monkeypatch,workspace)


def test_stale_review_refuses_before_default_write(pg_local_compat_process):
 pg_local_compat_process(_check_stale_review_refuses_before_default_write)


def _check_companion_mismatch_cannot_supply_a_default(monkeypatch,tmp_path,workspace):
 from tests.test_stack_decisions import _case_companion_mismatch_cannot_supply_a_default
 _case_companion_mismatch_cannot_supply_a_default(tmp_path,monkeypatch,workspace)


def test_companion_mismatch_cannot_supply_a_default(pg_local_compat_process):
 pg_local_compat_process(_check_companion_mismatch_cannot_supply_a_default)
