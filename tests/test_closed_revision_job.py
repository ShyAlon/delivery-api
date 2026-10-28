import asyncio
from types import SimpleNamespace
from copy import deepcopy
import pytest
from delivery_api.closed_revision_job import _options,execute_closed_revision_proposal,KIND


def options():
    return {'phase':'srs','gate_key':'gate-srs','gate_revision':2,'review_revision':'review',
        'draft_id':'draft','preflight_digest':'a'*64,'source_checkpoint_id':'checkpoint',
        'change_request':'Describe offline behavior','artifact_evidence':[{'phase':'srs','name':'srs.json','generation':1,'sha256':'c'*64}], 'extra_cost_acknowledged':True}


@pytest.mark.parametrize('change',[{'phase':'needs'},{'gate_key':'gate-architecture'},
    {'extra_cost_acknowledged':False},{'extra_cost_acknowledged':1},
    {'gate_revision':True},{'preflight_digest':'bad'},{'source_checkpoint_id':''}])
def test_durable_revision_options_reject_missing_ack_or_unimplemented_basis(change):
    with pytest.raises(ValueError):_options(options()|change)


def test_dispatch_uses_only_durable_reviewed_options_and_rechecks_source(monkeypatch):
    from delivery_api import closed_revision_job as module
    durable=options();before=deepcopy(durable);checks=[]
    cap=SimpleNamespace(task_id='task',validate=lambda store:checks.append(1))
    store=SimpleNamespace(worker_job=lambda id:{'kind':KIND,'execution_options':durable})
    monkeypatch.setattr(module,'closed_revision_preflight',lambda *args,**kwargs:{'review_digest':'a'*64})
    class Worker:
        async def execute(self,id,callback):callback(cap,'owned');return True
    def prepare(**kwargs):
        kwargs['validate_authority']()
        assert kwargs['phase']=='srs' and kwargs['change_request']=='Describe offline behavior'
        assert kwargs['checkpoint_id']=='checkpoint'
        return {'committed':False,'requires_fresh_review':True}
    result=asyncio.run(execute_closed_revision_proposal(store,Worker(),'job',read_artifact_evidence=lambda *args:[],
        log_factory=lambda *args:None,prepare=prepare))
    assert durable==before and len(checks)==2 and not result['source_committed'] and not result['restart_dispatched']


def test_stale_evidence_never_calls_producer(monkeypatch):
    from delivery_api import closed_revision_job as module
    cap=SimpleNamespace(task_id='task',validate=lambda store:None);calls=[]
    store=SimpleNamespace(worker_job=lambda id:{'kind':KIND,'execution_options':options()})
    monkeypatch.setattr(module,'closed_revision_preflight',lambda *args,**kwargs:{'review_digest':'b'*64})
    class Worker:
        async def execute(self,id,callback):callback(cap,'owned');return True
    with pytest.raises(ValueError,match='evidence changed'):
        asyncio.run(execute_closed_revision_proposal(store,Worker(),'job',read_artifact_evidence=lambda *args:[],
            log_factory=lambda *args:None,prepare=lambda **kwargs:calls.append(1)))
    assert calls==[]


@pytest.mark.parametrize('ack',[True,False])
def test_queue_retains_cost_ack_exact_draft_and_never_commits_source(tmp_path,monkeypatch,ack,pg_scoped_workspace):
    from contextlib import contextmanager
    from delivery_api import closed_revision_job as module
    from delivery_api.gate_revision import save_closed_revision,closed_revision_preflight
    from delivery_api.phases import PHASE_BY_KEY
    store=pg_scoped_workspace.store;
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(store,PostgresStore)
    tid=store.create_task('revision')['task_id']
    store.wait_at_gate(tid,'srs');waiting=store.task(tid)
    store.retain_decision_review(tid,'gate:gate-srs','review',{'task':waiting})
    store.decide_gate(tid,'gate-srs','approve',selected_option='Proceed')
    saved=save_closed_revision(store,tid,'gate-srs','review','Describe offline behavior')
    evidence=[{'phase':'srs','name':n,'generation':1,'sha256':'a'*64} for n in PHASE_BY_KEY['srs'].artifacts]
    prepared=closed_revision_preflight(store,tid,'gate-srs','review',saved['draft_id'],artifact_snapshot=evidence)
    before=store.task(tid);jobs=[];guard=[]
    @contextmanager
    def guarded(action):
        guard.append(action);yield
    monkeypatch.setattr(store,'_task_operation_guard',guarded)
    monkeypatch.setattr(store,'enqueue_worker',lambda task,kind,execution_options:jobs.append((task,kind,execution_options)) or 'job')
    monkeypatch.setattr(module.WorkerCapability,'capture',lambda *args:SimpleNamespace(validate=lambda s:s.task(tid)))
    kwargs=dict(gate_key='gate-srs',review_revision='review',draft_id=saved['draft_id'],
        reviewed_preflight_digest=prepared['review_digest'],source_checkpoint_id='checkpoint',artifact_snapshot=evidence,
        extra_cost_acknowledged=ack)
    if ack:
        assert module.queue_closed_revision_proposal(store,tid,**kwargs)=='job'
        assert jobs[0][2]['change_request']=='Describe offline behavior' and jobs[0][2]['extra_cost_acknowledged'] is True
    else:
        with pytest.raises(ValueError,match='acknowledgement'):module.queue_closed_revision_proposal(store,tid,**kwargs)
        assert jobs==[]
    assert store.task(tid)==before and guard==['task.rerun']


def test_preflight_requires_core_artifacts_but_not_conditional_companions(tmp_path, monkeypatch,pg_scoped_workspace):
    """Companions exist only when the phase produced them (semantic review ran /
    research_required slots exist): evidence without them is complete; evidence
    missing a core artifact still refuses."""
    from delivery_api.gate_revision import save_closed_revision, closed_revision_preflight
    for phase, gate, core, companion in (('srs', 'gate-srs', 'srs.json', 'srs.semantic-review.json'),
                                         ('architecture', 'gate-architecture', 'architecture.json', 'architecture.decisions.json')):
        store = pg_scoped_workspace.store; tid = store.create_task('revision '+phase)['task_id']
        store.wait_at_gate(tid, phase); waiting = store.task(tid)
        store.retain_decision_review(tid, f'gate:{gate}', 'review', {'task': waiting})
        store.decide_gate(tid, gate, 'approve', selected_option='Proceed')
        saved = save_closed_revision(store, tid, gate, 'review', 'Describe offline behavior')
        without_companion = [{'phase': phase, 'name': core, 'generation': 1, 'sha256': 'a' * 64}]
        prepared = closed_revision_preflight(store, tid, gate, 'review', saved['draft_id'],
                                             artifact_snapshot=without_companion)
        assert prepared['artifact_evidence'] == without_companion, f'{companion} is conditional, never forced'
        with pytest.raises(ValueError, match='complete canonical phase artifact evidence required'):
            closed_revision_preflight(store, tid, gate, 'review', saved['draft_id'],
                                      artifact_snapshot=[{'phase': phase, 'name': companion, 'generation': 1, 'sha256': 'a' * 64}])
