#!/usr/bin/env python3
"""No-spend hurdle checks. Real UI/app on disposable fixtures, then preview reads.

Run with the checkout venv. Optional PREVIEW_BROWSER_STATE points to a private
Playwright storage-state file. Never accepts a password or a database URL.
"""
import argparse,importlib.util,json,os,subprocess,sys,tempfile,zipfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[2]
if not (ROOT/'tests/conftest.py').is_file():ROOT=Path.cwd()
CASES={
 'Account Debug ZIP (UI, scoped allow/deny, revoked registry)':[
  'tests/test_account_debug_capability.py'],
 'Stop epoch router (real PG/engine store, no HTTP provider)':[
  'tests/test_stop_control_routing.py'],
 'Own-test transition (real disposable PG, atomic/idempotent)':[
  'tests/test_own_test_guard.py::test_explicit_transition_atomic_and_idempotent',
  'tests/test_own_test_guard.py::test_transition_refuses_active_work_and_other_caps',
  'tests/test_backoffice_job_stop.py::test_confirmed_hidden_running_job_no_longer_blocks_profile_transition'],
 'Answer/question pairing (real UI, scripted SSE/response race)':[
  'tests/test_ui_playwright.py::test_interview_history_binds_answer_to_input_when_next_prompt_arrives_first',
  'tests/test_ui_playwright.py::test_answer_response_cannot_pair_previous_answer_with_next_question'],
 'Final package (real create/download routes, zero-model producer)':[
  'tests/test_cloud_final_package.py','tests/test_hurdle_final_package_ui.py'],
 'Worker Jobs Stop/Delete (real UI/proxy, disposable tasks)':[
  'tests/test_backoffice_job_stop.py', 'tests/test_backoffice_shared_ui.py'],
}


def local_url(value):
    p=urlsplit(value)
    if p.scheme!='http' or p.hostname not in ('127.0.0.1','localhost','::1') or p.username or p.password or p.path not in ('','/') or p.query or p.fragment:
        raise ValueError('PREVIEW_URL must be a plain loopback HTTP origin, such as http://127.0.0.1:8001')
    return value.rstrip('/')


def fixture_suite(output):
    # The existing conftest must create its own PG. Strip all caller secrets and
    # live configuration before spawning it. A socket fence rejects off-machine
    # traffic, so a newly introduced accidental provider/JWKS call fails locally.
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','LANG','LC_ALL','SYSTEMROOT','PLAYWRIGHT_BROWSERS_PATH','PLAYWRIGHT_CHROMIUM_EXECUTABLE')}
    with tempfile.TemporaryDirectory(prefix='requi-hurdles-') as scratch:
        hooks=Path(scratch)
        (hooks/'sitecustomize.py').write_text('''import socket
_original=socket.socket.connect
_original_ex=socket.socket.connect_ex
def guard(self,address):
 if isinstance(address,tuple) and address[0] not in ('127.0.0.1','localhost','::1'):raise RuntimeError('hurdle suite forbids external network')
 return _original(self,address)
def guard_ex(self,address):
 if isinstance(address,tuple) and address[0] not in ('127.0.0.1','localhost','::1'):raise RuntimeError('hurdle suite forbids external network')
 return _original_ex(self,address)
socket.socket.connect=guard
socket.socket.connect_ex=guard_ex
''')
        (hooks/'hurdle_report.py').write_text('''import json,os
reports=[]
def pytest_runtest_logreport(report):reports.append({'nodeid':report.nodeid,'when':report.when,'outcome':report.outcome})
def pytest_sessionfinish(session,exitstatus):
 with open(os.environ['HURDLE_REPORT'],'w') as f:json.dump({'exitstatus':int(exitstatus),'reports':reports},f)
''')
        report=output/'fixture-results.json';env.update(PYTHONPATH=str(hooks),HURDLE_REPORT=str(report),REQUI_TEST_ARTIFACTS=str(output/'screenshots'))
        args=list(dict.fromkeys(item for group in CASES.values() for item in group))
        log=output/'fixture-suite.log'
        with log.open('w') as handle:
            result=subprocess.run([sys.executable,'-m','pytest','-q','-p','hurdle_report',*args],cwd=ROOT,env=env,stdout=handle,stderr=subprocess.STDOUT,timeout=600)
        if not report.exists():
            print('FAIL | fixture suite could not start | '+str(log));return False
        rows=json.loads(report.read_text())['reports']
        for label,nodes in CASES.items():
            selected=[r for r in rows if any(r['nodeid']==n or r['nodeid'].startswith(n+'::') or r['nodeid'].startswith(n+'[') for n in nodes)]
            failed=any(r['outcome']=='failed' for r in selected)
            skipped=any(r['outcome']=='skipped' for r in selected)
            calls=[r for r in selected if r['when']=='call' and r['outcome']=='passed']
            missing=any(not any(r['when']=='call' and r['outcome']=='passed' and (r['nodeid']==n or r['nodeid'].startswith(n+'::') or r['nodeid'].startswith(n+'[')) for r in selected) for n in nodes)
            status='FAIL' if failed else 'NOT CHECKED' if skipped or missing else 'PASS'
            print(f'{status} | {label} | disposable fixture proof only; {len(calls)} passed; log {log}',flush=True)
        return result.returncode==0 and not any(r['outcome']=='skipped' for r in rows)


def preview_checks(base,state,output):
    from playwright.sync_api import sync_playwright
    spec=importlib.util.spec_from_file_location('preview_smoke',ROOT/'scripts/ops/browser_smoke.py');smoke=importlib.util.module_from_spec(spec);spec.loader.exec_module(smoke)
    ok=True
    events=smoke.collect(base)
    if events is None:raise RuntimeError('Chromium is not installed')
    failures,_=smoke.classify(events)
    print(('FAIL' if failures else 'PASS')+' | running preview anonymous browser boot | '+('; '.join(failures) if failures else 'actual UI, expected anonymous 401s only'))
    ok=not failures
    with sync_playwright() as p:
        browser=p.chromium.launch()
        try:
            c=browser.new_context();health=c.request.get(base+'/health',timeout=10000)
            if health.status!=200 or health.json().get('ok') is not True:raise RuntimeError('preview health is not OK')
            print('PASS | running preview health | actual anonymous GET /health');c.close()
            if not state:
                print('NOT CHECKED | signed-in running preview / account Debug ZIP | PREVIEW_BROWSER_STATE absent; fixture UI proof above is separate')
                print('Optional local state setup: (umask 077; .venv/bin/python -m playwright codegen --save-storage="$HOME/preview-browser-state.json" '+base+')  (sign in locally, close; keep this file private; then PREVIEW_BROWSER_STATE=$HOME/preview-browser-state.json reruns the optional check)')
                return ok
            c=browser.new_context(storage_state=str(state),accept_downloads=True);page=c.new_page()
            # No task-start/answer/Stop/Delete/package-create calls are permitted.
            def restrict(route):
                r=route.request;u=urlsplit(r.url)
                allowed=(r.method in ('GET','HEAD') or r.method=='POST' and (u.path=='/api/client-log' or u.path.endswith('/debug.zip')))
                if u.netloc!=urlsplit(base).netloc or not allowed:route.abort()
                else:route.continue_()
            page.route('**/*',restrict)
            page.goto(base);identity=c.request.get(base+'/api/auth').json()
            if identity.get('authenticated') is not True:raise RuntimeError('configured browser state is expired or not signed in')
            tasks=c.request.get(base+'/api/tasks').json().get('tasks',[])
            candidate=next((t for t in tasks if t.get('engine_session_id')),None)
            if not candidate:
                print('NOT CHECKED | signed-in preview task Debug ZIP | no existing visible task with a recorded session; no run started')
            elif not identity.get('debug_features'):
                print('NOT CHECKED | signed-in preview task Debug ZIP | this account does not have debug.read')
            else:
                tid=candidate['task_id'];page.goto(base+'/tasks/'+tid+'/view');page.locator('#accountbutton').click();page.locator('#opendebug').click()
                with page.expect_download(timeout=30000) as pending:page.locator('#downloaddebug').click()
                path=output/'preview-account-debug.zip';pending.value.save_as(str(path))
                with zipfile.ZipFile(path) as archive:
                    if archive.testzip() is not None or json.loads(archive.read('manifest.json'))['task_id']!=tid:raise RuntimeError('downloaded debug ZIP failed integrity/binding')
                print('PASS | signed-in preview task Debug ZIP | actual account-menu UI download; private output '+str(path))
            print('NOT CHECKED | running preview mutation hurdles | Stop/Delete, own-test transition, answer submission and package creation only exercised on disposable fixtures; existing real tasks unchanged')
            c.close()
        finally:browser.close()
    return ok


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--preview-url',default=os.environ.get('PREVIEW_URL','http://127.0.0.1:'+os.environ.get('PREVIEW_EXEC_PORT','8001')));parser.add_argument('--output',type=Path);args=parser.parse_args()
    try:
        base=local_url(args.preview_url)
        if not (ROOT/'tests/conftest.py').is_file():raise ValueError('run from the delivery-api checkout with tests and scripts/ops present')
        if Path(sys.executable).resolve()==Path('/usr/bin/python3').resolve():raise ValueError('use the checkout .venv/bin/python')
        for name in ('pytest','pgserver','psycopg','playwright.sync_api'):__import__(name.split('.')[0])
        state=os.environ.get('PREVIEW_BROWSER_STATE')
        if state and not Path(state).is_file():raise ValueError('PREVIEW_BROWSER_STATE is configured but the file is missing')
        if state and Path(state).stat().st_mode&0o077:raise ValueError('PREVIEW_BROWSER_STATE contains login cookies: chmod 600 that file before use')
        os.umask(0o077)
        output=args.output or Path(tempfile.mkdtemp(prefix='requi-hurdle-results-'));output.mkdir(parents=True,exist_ok=True)
        print('No-spend validation. No live DB connection or task mutation. Output is private: '+str(output),flush=True)
        fixtures=fixture_suite(output)
        try:live=preview_checks(base,Path(state) if state else None,output)
        except Exception as error:
            print('FAIL | running preview checks | '+str(error),flush=True)
            live=False
        return 0 if fixtures and live else 1
    except Exception as error:
        print('FAIL | validation setup/runtime | '+str(error),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
