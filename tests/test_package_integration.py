"""Delivery-to-producer handoff with real CLI, no model call or private account."""
import json,os,pytest
from pathlib import Path
from fastapi.testclient import TestClient
from delivery_api.store import SQLiteStore
import delivery_api.app as module

@pytest.mark.producer_integration
@pytest.mark.parametrize("reviewed", [False, True])
def test_package_endpoint_calls_real_zero_model_export_and_downloads(tmp_path,monkeypatch,reviewed):
 import shutil,pytest
 from vmodel_contracts.runtime import artifacts_root
 producer=Path(os.environ.get('SPEC_GENERATOR_DIR',Path(__file__).parents[2].parent/'spec-generator'))
 if not (producer/'.venv/bin/python').is_file():pytest.skip('requires local spec-generator export environment')
 root=artifacts_root(str(tmp_path/'artifacts'))/'s';root.mkdir(parents=True)
 for folder in ('interview','spec'):shutil.copytree(Path(__file__).parent/'fixtures/coding-package'/folder,root/folder)
 interview=root/'interview';spec=root/'spec'
 if not reviewed:(spec/'findings-ledger.json').unlink()
 store=SQLiteStore(str(tmp_path/'db'));t=store.create_task('Ready','',task_id='ready');store.set_engine_session('ready','s')
 for p in t['phases']:store.update_phase('ready',p['phase_key'],'approved')
 store._db.execute("UPDATE tasks SET state='done' WHERE task_id='ready'");store._db.commit()
 monkeypatch.setattr(module,'store',store);monkeypatch.setattr(module.orchestrator,'store',store)
 monkeypatch.setenv('SPEC_GENERATOR_DIR',str(producer));monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path/'artifacts'))

 c=TestClient(module.app);reply=c.post('/api/tasks/ready/packages',json={});
 if not reviewed:
  assert reply.status_code==409 and 'findings ledger missing' in reply.text
  return
 assert reply.status_code==200,reply.text
 result=reply.json();assert 'spec/AGENTS.md' in result['contents']
 assert 'spec/validation.json' in result['contents']
 download=c.get(result['url']);assert download.status_code==200;assert download.headers['content-type']=='application/zip'
 # Actual repeat immutable and actual historical download remains available.
 second=c.post('/api/tasks/ready/packages',json={}).json();assert second['package_id']==result['package_id']
