"""Review and feedback use PG-owned native statements, not SQLite adaptation."""
import pytest
from delivery_api.tenancy import tenant_context


def test_native_review_draft_feedback_boundary(pg_workspace_factory,monkeypatch, pg_bridge_poison):
    _Connection = pg_bridge_poison
    from delivery_api.store import SQLiteStore
    with pg_workspace_factory() as workspace,tenant_context(workspace.scope):
        store=workspace.store
        tid=store.create_task('Native reviews')['task_id']
        def forbidden(*args,**kwargs):
            raise AssertionError('review/feedback still uses SQLite compatibility path')
        bridge = _Connection.execute
        def reject_family(self,sql,parameters=()):
            if any(table in sql for table in ('review_comments','decision_reviews','decision_drafts','answer_feedback')):
                forbidden()
            return bridge(self,sql,parameters)
        with monkeypatch.context() as patch:
            # Keep real outer operation guard SQL, which is a separate family.
            patch.setattr(_Connection,'execute',reject_family)
            for name in ('add_review_comment','review_comments','retain_decision_review','decision_review',
                         'save_decision_draft','decision_draft','list_feedback','upsert_feedback',
                         '_feedback_row','update_feedback','delete_feedback'):
                patch.setattr(SQLiteStore,name,forbidden)
            cid=store.add_review_comment(tid,'gate-intent','r1','actor','Keep')
            assert store.review_comments(tid,'gate-intent','r1')[0]['id']==cid
            payload={'choice':'keep'}
            store.retain_decision_review(tid,'gate-intent','r1',payload)
            assert store.decision_review(tid,'gate-intent','r1')==payload
            with pytest.raises(ValueError,match='conflicting retained'):
                store.retain_decision_review(tid,'gate-intent','r1',{'choice':'change'})
            draft=store.save_decision_draft(tid,'gate-intent','r1',payload)
            assert store.decision_draft(tid,'gate-intent','r1',draft)==payload
            row=store.upsert_feedback(tid,'input','answer','Text',1,'Good','actor')
            assert store.list_feedback(tid)[0]['id']==row['id']
            assert store.update_feedback(tid,row['id'],'actor',thumb=-1)['thumb']==-1
            with pytest.raises(PermissionError):store.delete_feedback(tid,row['id'],'other')
            store.delete_feedback(tid,row['id'],'actor')
            assert store.list_feedback(tid)==[]


def test_native_feedback_audit_failure_rolls_back(pg_workspace_factory,monkeypatch):
    with pg_workspace_factory() as workspace,tenant_context(workspace.scope):
        store=workspace.store
        tid=store.create_task('Atomic feedback')['task_id']
        original=store.audit
        def fail(actor,action,*args,**kwargs):
            if action.startswith('feedback.'):raise RuntimeError('audit down')
            return original(actor,action,*args,**kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(store,'audit',fail)
            with pytest.raises(RuntimeError,match='audit down'):
                store.upsert_feedback(tid,'input','answer','Text',1,None,'actor')
        assert store.list_feedback(tid)==[]


def test_concurrent_conflicting_review_pin_keeps_one_revision(pg_workspace_factory):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    with pg_workspace_factory() as workspace:
        with tenant_context(workspace.scope):
            tid=workspace.store.create_task('Concurrent review pin')['task_id']
        ready=threading.Barrier(2)
        def pin(choice):
            with tenant_context(workspace.scope):
                ready.wait(timeout=5)
                try:
                    workspace.store.retain_decision_review(tid,'gate-intent','r1',{'choice':choice})
                    return 'kept'
                except ValueError as error:
                    assert 'conflicting retained' in str(error)
                    return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(pin,['one','two']))
        assert sorted(results)==['conflict','kept']
        with tenant_context(workspace.scope):
            assert workspace.store.decision_review(tid,'gate-intent','r1')['choice'] in ('one','two')
