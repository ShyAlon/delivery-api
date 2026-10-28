import pytest


def test_retained_review_exact_replay_is_idempotent_conflicting_revision_rejected(tmp_path,pg_scoped_workspace):
    s=pg_scoped_workspace.store;tid=s.create_task('immutable')['task_id']
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(s,PostgresStore)
    payload={'task':s.task(tid),'documents':[{'name':'needs.json','view':{'content':'original'}}]}
    s.retain_decision_review(tid,'gate:gate-needs','same',payload)
    s.retain_decision_review(tid,'gate:gate-needs','same',payload)
    changed={**payload,'documents':[{'name':'needs.json','view':{'content':'changed'}}]}
    with pytest.raises(ValueError,match='conflicting'):
        s.retain_decision_review(tid,'gate:gate-needs','same',changed)
    assert s.decision_review(tid,'gate:gate-needs','same')==payload
    s.retain_decision_review(tid,'gate:gate-needs','new',changed)
    assert s.decision_review(tid,'gate:gate-needs','new')==changed


def test_retained_review_survives_reopen_and_is_hidden_from_other_owner(pg_scoped_workspace,pg_workspace_factory):
    from delivery_api.postgres_store import PostgresStore
    from delivery_api.tenancy import tenant_context
    s=pg_scoped_workspace.store
    tid=s.create_task('Private retained review')['task_id']
    payload={'documents':[{'name':'needs.json','view':{'content':'owner-only evidence'}}]}
    s.retain_decision_review(tid,'gate:gate-needs','revision',payload)
    reopened=PostgresStore(pg_scoped_workspace.dsn.replace('user=sandbox','user=delivery_app'))
    assert reopened.decision_review(tid,'gate:gate-needs','revision')==payload
    with pg_workspace_factory() as other:
        with tenant_context(other.scope):
            assert other.store.decision_review(tid,'gate:gate-needs','revision') is None
    assert s.decision_review(tid,'gate:gate-needs','revision')==payload
