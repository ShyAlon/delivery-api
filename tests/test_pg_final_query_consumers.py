"""Whole production consumer inventory before deleting the PG query bridge."""
import ast
from pathlib import Path
from uuid import uuid4
import pytest
from delivery_api.tenancy import tenant_context


def test_no_production_store_bridge_consumers_remain():
    root=Path(__file__).parents[1]/'src'/'delivery_api'
    found=[]
    for path in root.rglob('*.py'):
        if path.name in ('store.py','libsql_store.py','run_metrics.py'):continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node,ast.Attribute) and node.attr=='_db' and isinstance(node.ctx,ast.Load):
                found.append((path.name,node.lineno))
    assert found==[]


def test_entire_pg_bridge_can_be_disabled_for_user_work(pg_workspace_factory,monkeypatch, pg_bridge_poison):
    _Connection = pg_bridge_poison
    with pg_workspace_factory() as w,tenant_context(w.scope):
        s=w.store
        def reject(*args,**kw):raise AssertionError('PG query bridge called')
        with monkeypatch.context() as patch:
            patch.setattr(_Connection,'execute',reject)
            tid=s.create_task('Bridge-free task')['task_id']
            s.set_engine_session(tid,'final-query-'+uuid4().hex)
            link=s.create_link('Native link')
            assert s.link(link['token'])['run_id']==link['run_id']
            snapshot=s.operator_workspace_snapshot()
            assert snapshot['owner_id']==w.scope.owner_id
            assert snapshot['global_operator_access'] is False
            assert snapshot['credit_adjustment_available'] is False
            assert snapshot['sessions'][0]['task_id']==tid
            assert s.prepare_next_phase(tid,'invalid','fixture','ollama:test') is None
            job=s.enqueue_worker(tid,'fixture');assert s.claim_worker(job)
            s.finish_worker(job);s.delete_task(tid)
            assert s.task(tid) is None


def test_pg_query_bridge_class_and_assignment_are_gone():
    import delivery_api.postgres_store as module
    assert not hasattr(module,'_Connection')
    source=Path(module.__file__).read_text()
    assert not any(isinstance(n,ast.Attribute) and n.attr=='_db' for n in ast.walk(ast.parse(source)))


def test_retired_bridge_test_tripwire_is_fail_closed(pg_bridge_poison):
    from delivery_api.postgres_store import PostgresStore
    with pytest.raises(AssertionError,match='retired PG query bridge called'):
        PostgresStore.__new__(PostgresStore)._db.execute('SELECT 1')
