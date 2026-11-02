"""Retained evidence contracts against isolated real PostgreSQL."""
from uuid import uuid4
import pytest
from vmodel_contracts.progress_signal import ProgressSignal


def test_progress_replay_gap_events_and_snapshot(evidence_store):
    store = evidence_store
    task_id = store.create_task('Progress parity')['task_id']
    session_id = str(uuid4())
    store.set_engine_session(task_id, session_id)
    store.update_phase(task_id, 'intent', 'running')
    signal = ProgressSignal(event_id=str(uuid4()), cursor=1, task_id=task_id,
        session_id=session_id, phase_key='intent', stage='parity', label='First step',
        status='started', completed=1, total=3)
    assert store.ingest_progress(signal, 'interview') is True
    assert store.ingest_progress(signal, 'interview') is False
    assert store.progress_cursor(task_id, 'interview', session_id) == 1
    gap = signal.model_copy(update={'event_id':str(uuid4()),'cursor':3})
    with pytest.raises(ValueError, match='cursor gap'):
        store.ingest_progress(gap, 'interview')
    assert store.progress_cursor(task_id, 'interview', session_id) == 1
    second = signal.model_copy(update={'event_id':str(uuid4()),'cursor':2,'label':'Second step','completed':2})
    assert store.ingest_progress(second, 'interview') is True
    snapshot = store.task_snapshot(task_id)
    phase = next(p for p in snapshot['task']['phases'] if p['phase_key']=='intent')
    assert (phase['detail'],phase['completed'],phase['total']) == ('Second step',2,3)
    events = [e for e in snapshot['events'] if e['kind']=='progress']
    assert [e['payload']['cursor'] for e in events] == [1,2]
    assert all(e['payload']['source']=='interview' for e in events)
    assert store.events(task_id, after=events[0]['id'])[-1]['id'] == events[1]['id']
    assert store.task_snapshot('absent') is None


def test_reviews_drafts_revision_pins_and_audit(evidence_store):
    store = evidence_store
    task_id = store.create_task('Review parity')['task_id']
    comment_id = store.add_review_comment(task_id,'gate-intent','revision-1','tester','Keep this evidence')
    comments = store.review_comments(task_id,'gate-intent','revision-1')
    assert comments[0]['id'] == comment_id
    assert comments[0]['comment'] == 'Keep this evidence'
    assert store.review_comments(task_id,'gate-intent','revision-2') == []
    payload = {'decision':'keep','evidence':['one','two']}
    store.retain_decision_review(task_id,'gate-intent','revision-1',payload)
    store.retain_decision_review(task_id,'gate-intent','revision-1',payload)
    with pytest.raises(ValueError, match='conflicting retained'):
        store.retain_decision_review(task_id,'gate-intent','revision-1',{'decision':'changed'})
    assert store.decision_review(task_id,'gate-intent','revision-1') == payload
    draft = store.save_decision_draft(task_id,'gate-intent','revision-1',{'choice':'approve'})
    assert store.decision_draft(task_id,'gate-intent','revision-1',draft) == {'choice':'approve'}
    assert store.decision_draft(task_id,'gate-intent','revision-2',draft) is None
    entry = store.audit('tester','parity.checked',task_id,{'pinned':True})
    assert any(e['id']==entry and e['detail']=={'pinned':True} for e in store.audit_entries(task_id))


def test_feedback_crud_actor_boundary_and_task_snapshot(evidence_store):
    store = evidence_store
    task_id = store.create_task('Feedback parity')['task_id']
    row = store.upsert_feedback(task_id,'input-1','answer','Original answer',1,'Useful','actor-a')
    updated = store.upsert_feedback(task_id,'input-1','answer','Updated answer',-1,'Revise','actor-a')
    assert updated['id'] == row['id']
    assert updated['content'] == 'Updated answer'
    assert len(store.list_feedback(task_id)) == 1
    with pytest.raises(PermissionError):
        store.update_feedback(task_id,row['id'],'actor-b',thumb=1)
    assert store.list_feedback(task_id)[0]['thumb'] == -1
    updated = store.update_feedback(task_id,row['id'],'actor-a',thumb=1,comment='Fixed')
    assert (updated['thumb'],updated['comment']) == (1,'Fixed')
    with pytest.raises(ValueError):
        store.upsert_feedback(task_id,'input-2','answer','Invalid',2,None,'actor-a')
    with pytest.raises(PermissionError):
        store.delete_feedback(task_id,row['id'],'actor-b')
    store.delete_feedback(task_id,row['id'],'actor-a')
    assert store.list_feedback(task_id) == []
    actions = {e['action'] for e in store.audit_entries(task_id)}
    assert {'feedback.upsert','feedback.update','feedback.delete'} <= actions


def test_pg_cross_owner_evidence_is_invisible_and_writes_refused(pg_workspace_factory):
    from delivery_api.tenancy import tenant_context
    import psycopg
    with pg_workspace_factory() as first, pg_workspace_factory() as second:
        store = first.store
        with tenant_context(first.scope):
            task_id = store.create_task('Private evidence')['task_id']
            store.add_event(task_id,'private','Secret event')
            store.add_review_comment(task_id,'gate-intent','r1','owner-a','Secret comment')
            store.retain_decision_review(task_id,'gate-intent','r1',{'secret':True})
            draft = store.save_decision_draft(task_id,'gate-intent','r1',{'secret':'draft'})
            feedback = store.upsert_feedback(task_id,'i1','answer','Secret answer',1,None,'owner-a')
        with tenant_context(second.scope):
            assert store.task_snapshot(task_id) is None
            assert store.events(task_id) == []
            assert store.audit_entries(task_id) == []
            assert store.review_comments(task_id,'gate-intent','r1') == []
            assert store.decision_review(task_id,'gate-intent','r1') is None
            assert store.decision_draft(task_id,'gate-intent','r1',draft) is None
            assert store.list_feedback(task_id) == []
            with pytest.raises(KeyError):
                store.update_feedback(task_id,feedback['id'],'owner-a',thumb=-1)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                store.add_review_comment(task_id,'gate-intent','r1','owner-b','Attack')
        with tenant_context(first.scope):
            assert len(store.review_comments(task_id,'gate-intent','r1')) == 1
            assert store.list_feedback(task_id)[0]['thumb'] == 1


def test_pg_running_fixture_is_revoked_without_weakening_delete(pg_workspace_factory):
    from delivery_api.tenancy import tenant_context
    with pg_workspace_factory() as workspace:
        with tenant_context(workspace.scope):
            task_id = workspace.store.create_task('Running fixture')['task_id']
            workspace.store.update_phase(task_id,'intent','running')
            with pytest.raises(ValueError, match='explicit confirmation'):
                workspace.store.delete_task(task_id,confirm=False)
    with workspace.admin() as connection:
        assert connection.execute('SELECT count(*) FROM auth.sessions WHERE id=%s',
            (workspace.scope.session_id,)).fetchone()[0] == 0
        assert connection.execute('SELECT state FROM tasks WHERE task_id=%s',
            (task_id,)).fetchone()[0] == 'stop_requested'


def test_pg_published_artifact_snapshot_and_cross_owner_bytes(pg_workspace_factory, tmp_path):
    import hashlib
    from delivery_api.tenancy import tenant_context
    with pg_workspace_factory() as first, pg_workspace_factory() as second:
        store = first.store
        path = tmp_path/'srs.json'
        body = b'{"requirements":["kept"]}'
        path.write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        with tenant_context(first.scope):
            task_id = store.create_task('Artifact projection')['task_id']
            store.publish_task_revision(task_id,[{'phase_key':'spec','name':'srs.json','ref':str(path),
                'validation_report':{'validator':'V','validator_version':'v==1','valid':True,
                                     'error':None,'sha256':digest}}],writer_instance='fixture#1')
            manifest = store.manifest_snapshot(task_id)
            assert manifest['rows'][0]['sha256'] == digest
            assert store.read_artifact_bytes(task_id,'spec','srs.json') == body
            assert store.read_artifact_bytes_sha(digest) == body
            assert store.task_snapshot(task_id)['task']['task_id'] == task_id
            assert store.manifest_ready(task_id) is True
        with tenant_context(second.scope):
            assert store.manifest_snapshot(task_id) is None
            with pytest.raises(KeyError):
                store.read_artifact_bytes_sha(digest)
        with tenant_context(first.scope):
            path.unlink()
            assert store.read_artifact_bytes(task_id,'spec','srs.json') == body
