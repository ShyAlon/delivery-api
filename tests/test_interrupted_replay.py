"""A crash cannot leave an in-memory recorded replay blocking deletion forever."""
from delivery_api.simulation import Simulation
from pg_storage_guard import assert_pg_storage_only
from delivery_api.postgres_store import PostgresStore


def test_server_restart_marks_orphaned_replay_stopped_and_allows_delete(tmp_path,pg_scoped_workspace):
    first=pg_scoped_workspace.store
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(first,PostgresStore)
    task=first.create_task('Recorded run','brief');tid=task['task_id']
    first.configure_simulation(tid,str(tmp_path/'source'),'ollama/phi4')
    first.update_phase(tid,'intent','running')
    first.wait_at_gate(tid,'intent')
    assert first.task(tid)['state']=='waiting_gate'
    # A new store and Simulation have no in-memory worker after process death.
    reopened=PostgresStore(pg_scoped_workspace.dsn.replace('user=sandbox','user=delivery_app'));sim=Simulation(reopened)
    sim.reconcile_interrupted()
    stopped=reopened.task(tid)
    assert stopped['state']=='failed' and stopped['simulated']
    assert any(e['kind']=='interrupted' for e in reopened.events(tid))
    sim.reconcile_interrupted()
    assert len([e for e in reopened.events(tid) if e['kind']=='interrupted'])==1
    reopened.delete_task(tid)
    assert reopened.task(tid) is None


def test_replay_reconciliation_does_not_change_regular_tasks(tmp_path,pg_scoped_workspace):
    store=pg_scoped_workspace.store
    task=store.create_task('Real run','brief');tid=task['task_id']
    store.update_phase(tid,'intent','running')
    Simulation(store).reconcile_interrupted()
    assert store.task(tid)['state']=='running'


def test_partial_recording_finishes_replay_without_live_task(tmp_path,pg_scoped_workspace):
    import asyncio,json
    source=tmp_path/'recording';source.mkdir()
    (source/'usage.jsonl').write_text(json.dumps({'stage':'srs','provider':'ollama','model':'phi4',
                                                  'ts':'2026-09-29T10:00:00+03:00','prompt_tokens':1,'completion_tokens':2})+'\n')
    store=pg_scoped_workspace.store;sim=Simulation(store)
    task=sim.import_run(source,'Partial run');tid=task['task_id']
    asyncio.run(sim.replay(tid,None))
    result=store.task(tid)
    assert result['state']=='failed' and result['active_phase']=='srs'
    assert 'Recording ended' in next(p['detail'] for p in result['phases'] if p['phase_key']=='srs')
    store.delete_task(tid)
    assert store.task(tid) is None


def test_replay_fixture_uses_restricted_pg(pg_scoped_workspace):
    import pytest
    from delivery_api.postgres_store import PostgresStore
    assert isinstance(pg_scoped_workspace.store,PostgresStore)
    assert_pg_storage_only()


def test_restart_reconciliation_cannot_stop_another_owners_replay(pg_scoped_workspace,pg_workspace_factory,tmp_path):
    from delivery_api.tenancy import tenant_context
    with pg_workspace_factory() as other:
        with tenant_context(other.scope):
            tid=other.store.create_task('Other replay')['task_id']
            other.store.configure_simulation(tid,str(tmp_path/'other-source'),'ollama/phi4')
            other.store.update_phase(tid,'intent','running')
        Simulation(pg_scoped_workspace.store).reconcile_interrupted()
        with tenant_context(other.scope):
            assert other.store.task(tid)['state']=='running'
            assert not any(e['kind']=='interrupted' for e in other.store.events(tid))
