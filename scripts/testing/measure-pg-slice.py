"""Scoped cold/warm measurement; see docs/testing/postgres-workspaces.md."""
import json, os, subprocess, sys, time
from pathlib import Path
root=str(Path(__file__).resolve().parents[2])
scopes={
 'pg_shared_contract':['tests/test_tasks.py::test_task_management_api_structured_collisions_and_delete','tests/test_ui_playwright.py::test_task_filter_sort_rename_and_delete'],
 'postgres_slice':['tests/test_postgres_workspace.py::test_task_api_lifecycle_and_owner_isolation','tests/test_postgres_workspace.py::test_pg_dashboard_real_browser'],
}
if '--task-browser' in sys.argv:
 scopes={
  'pg_shared_browser':['tests/test_ui_playwright.py::test_task_filter_sort_rename_and_delete','tests/test_ui_playwright.py::test_create_task_dialog_keyboard_submit_textarea_and_cancel'],
  'postgres_browser_parity':['tests/test_postgres_task_browser.py'],
 }
if '--evidence' in sys.argv:
 scopes={
  'postgres_evidence_parity':['tests/test_store_evidence_parity.py'],
 }
results=[]
for name, tests in scopes.items():
 code='''import json,time,pytest\nresults=[]\nfor label in ('cold','warm'):\n start=time.monotonic();rc=pytest.main(%r);results.append({'mode':label,'elapsed_s':round(time.monotonic()-start,3),'exit_code':int(rc)})\nprint('MEASURE_JSON='+json.dumps(results))\n'''%(['-q','-s']+tests)
 start=time.monotonic()
 run=subprocess.run([sys.executable,'-c',code],cwd=root,env={**os.environ,'PLAYWRIGHT_CHROMIUM_EXECUTABLE':os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE','/usr/bin/google-chrome')},text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 open('/tmp/'+name+'-timing.log','w').write(run.stdout)
 measured=json.loads(next(line.split('=',1)[1] for line in run.stdout.splitlines() if line.startswith('MEASURE_JSON=')))
 results.append({'scope':name,'tests':tests,'process_wall_s':round(time.monotonic()-start,3),'runs':measured})
 print(name,measured,flush=True)
open('/tmp/pg-slice-timing.json','w').write(json.dumps(results,indent=2))

sys.exit(1 if any(r['exit_code'] for scope in results for r in scope['runs']) else 0)
