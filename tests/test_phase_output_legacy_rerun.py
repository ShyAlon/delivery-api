"""Legacy SQLite-only rerun contract, retained until generation/session policy is resolved.

PG explicitly refuses prepare_rerun. Keep the original local behavior regression
separate rather than emulate or weaken that refusal in migrated fixtures.
"""
import json
from fastapi.testclient import TestClient
from delivery_api import app as module
from delivery_api.store import SQLiteStore


def test_rerun_does_not_relabel_old_canonical_or_partial_as_current(tmp_path,monkeypatch):
    root=tmp_path/'artifacts';folder=root/'session-3'/'spec';folder.mkdir(parents=True)
    store=SQLiteStore(str(tmp_path/'rerun.db'));tid=store.create_task('Rerun freshness','brief')['task_id']
    store.set_engine_session(tid,'session-3')
    for phase in ('intent','conops','needs'):store.update_phase(tid,phase,'approved')
    store.update_phase(tid,'srs','running')
    (folder/'srs.json').write_text(json.dumps({'functional':[]}))
    (folder/'srs_fills.partial.json').write_text(json.dumps({'R-old':{'statement':'Stale'}}))
    store.update_phase(tid,'srs','awaiting_approval')
    # A new generation begins, but the old files remain under the same session.
    store.prepare_rerun(tid,'srs')
    import time
    time.sleep(.02)
    store.update_phase(tid,'srs','running')
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(root));monkeypatch.setattr(module,'store',store)
    data=TestClient(module.app).get(f'/api/tasks/{tid}/phase-output').json()
    assert data['status']=='waiting' and data['items']==[]


