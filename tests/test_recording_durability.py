import asyncio,json,shutil
from uuid import uuid4
from delivery_api.simulation import Simulation


def test_replay_uses_retained_recording_after_source_loss(tmp_path,pg_scoped_workspace):
    source=tmp_path/'upload';source.mkdir();sid='recorded-'+uuid4().hex
    row=dict(session_id=sid,stage='tests',provider='fake',model='fake',ts='2026-11-04T00:00:00Z')
    (source/'usage.jsonl').write_text(json.dumps(row)+'\n');(source/'tests.json').write_text('[]')
    simulation=Simulation(pg_scoped_workspace.store);task=simulation.import_run(source)
    assert task['source_dir']=='postgres:recording'
    shutil.rmtree(source)
    asyncio.run(simulation.replay(task['task_id'],None))
    assert pg_scoped_workspace.store.read_artifact_bytes(task['task_id'],'tests','tests.json')==b'[]'
    assert any(e['kind']=='complete' for e in pg_scoped_workspace.store.events(task['task_id']))
