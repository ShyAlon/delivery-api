"""A crash cannot leave an in-memory recorded replay blocking deletion forever."""
from delivery_api.simulation import Simulation
from delivery_api.store import SQLiteStore


def test_server_restart_marks_orphaned_replay_stopped_and_allows_delete(tmp_path):
    path=str(tmp_path/'restart.db');first=SQLiteStore(path)
    task=first.create_task('Recorded run','brief');tid=task['task_id']
    first.configure_simulation(tid,str(tmp_path/'source'),'ollama/phi4')
    first.update_phase(tid,'intent','running')
    first.wait_at_gate(tid,'intent')
    assert first.task(tid)['state']=='waiting_gate'
    # A new store and Simulation have no in-memory worker after process death.
    reopened=SQLiteStore(path);sim=Simulation(reopened)
    sim.reconcile_interrupted()
    stopped=reopened.task(tid)
    assert stopped['state']=='failed' and stopped['simulated']
    assert any(e['kind']=='interrupted' for e in reopened.events(tid))
    sim.reconcile_interrupted()
    assert len([e for e in reopened.events(tid) if e['kind']=='interrupted'])==1
    reopened.delete_task(tid)
    assert reopened.task(tid) is None


def test_replay_reconciliation_does_not_change_regular_tasks(tmp_path):
    store=SQLiteStore(str(tmp_path/'regular.db'))
    task=store.create_task('Real run','brief');tid=task['task_id']
    store.update_phase(tid,'intent','running')
    Simulation(store).reconcile_interrupted()
    assert store.task(tid)['state']=='running'


def test_partial_recording_finishes_replay_without_live_task(tmp_path):
    import asyncio,json
    source=tmp_path/'recording';source.mkdir()
    (source/'usage.jsonl').write_text(json.dumps({'stage':'srs','provider':'ollama','model':'phi4',
                                                  'ts':'2026-09-29T10:00:00+03:00','prompt_tokens':1,'completion_tokens':2})+'\n')
    store=SQLiteStore(str(tmp_path/'partial.db'));sim=Simulation(store)
    task=sim.import_run(source,'Partial run');tid=task['task_id']
    asyncio.run(sim.replay(tid,None))
    result=store.task(tid)
    assert result['state']=='failed' and result['active_phase']=='srs'
    assert 'Recording ended' in next(p['detail'] for p in result['phases'] if p['phase_key']=='srs')
    store.delete_task(tid)
    assert store.task(tid) is None
