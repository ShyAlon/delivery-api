import time
from uuid import uuid4
import pytest
from delivery_api.tenancy import tenant_context
from delivery_api.runner import ActiveWork
from vmodel_contracts.interactive import EngineSnapshot,ProcessStatus,NextStep

@pytest.mark.parametrize('width,height',[(1475,780),(390,844)])
def test_real_task_page_running_needs_has_no_false_warning(pg_browser,monkeypatch,width,height):
 from playwright.sync_api import expect
 import delivery_api.app as m
 page,base,w=pg_browser
 with tenant_context(w.scope):
  task=w.store.create_task('Cheater running Needs '+str(width));tid=task['task_id'];sid=str(uuid4())
  w.store.set_engine_session(tid,sid)
  for phase in ('intent','conops'):w.store.update_phase(tid,phase,'done')
  w.store.update_phase(tid,'needs','running',detail='Stakeholder Needs 1 out of 7',completed=1,total=7)
 monkeypatch.setattr(m.runner,'active_work',{tid:ActiveWork('subprocess','needs','running',time.time())})
 monkeypatch.setattr(m.engine_client,'snapshot',lambda _:EngineSnapshot(session_id=sid,task_id=tid,sequence=28,process_status=ProcessStatus.COMPLETED,next_step=NextStep.COMPLETE))
 page.route('**/api/model-routes',lambda r:r.fulfill(json={'routes':[],'managed':True}))
 page.set_viewport_size({'width':width,'height':height});page.goto(base+'/tasks/'+tid+'/view')
 expect(page.locator('.live-indicator')).to_contain_text('Working')
 expect(page.locator('.task-error-state')).to_have_count(0)
 expect(page.get_by_text('Task needs attention',exact=False)).to_have_count(0)
 page.screenshot(path='/tmp/generator-owner-'+str(width)+'.png',full_page=False)
 # A real missing owner is still shown as unavailable after a new task GET.
 m.runner.active_work.pop(tid);page.reload()
 expect(page.locator('.task-error-state')).to_contain_text('No live work is attached')
