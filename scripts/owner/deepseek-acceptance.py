#!/usr/bin/env python3
"""Owner-run real signed-in acceptance. Dry by default; no provider call in dry mode."""
import argparse
import datetime
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import time
import threading
from urllib.parse import urlsplit
import uuid

ROOT=Path(__file__).resolve().parents[2]
IDEA='A simple web app for a solo tutor to manage students and lesson bookings. Each booking has a student, a date and time, and a short note. No payments, AI features, integrations or public signup.'
ANSWER='I am the only tutor and user. I create student records with a name and email, then book one-hour lessons in my local timezone. I need to see upcoming bookings and edit or cancel them. A double booking should be rejected. No payments, messaging or external calendars are needed.'

class Refused(RuntimeError):pass

class TransportUnavailable(Refused):pass


def positive(value):
    try:n=Decimal(str(value))
    except Exception:raise Refused('A finite positive cost ceiling is required') from None
    if not n.is_finite() or n<=0:raise Refused('A finite positive cost ceiling is required')
    return n


def estimate(pin,input_tokens=20000,output_tokens=6000,compute_ms=60000):
    """Planning scenario, never a promise or authorization to spend."""
    row=pin['payload']['row'];formula=row['retail_formula']
    scale=positive(row['expense_units_per_usd'])
    expense=(Decimal(max(row.get('expense_cache_miss',row['expense_input']),row['expense_input']))*input_tokens+Decimal(row['expense_output'])*output_tokens)/scale
    credits=Decimal(str(formula['price_per_token']))*(input_tokens+output_tokens)+Decimal(str(formula['price_per_10ms']))*Decimal(compute_ms)/10
    return {'model':row['model'],'pin_revision':pin['revision'],'scenario_input_tokens':input_tokens,
        'scenario_output_tokens':output_tokens,'scenario_active_compute_ms':compute_ms,
        'provider_usd_scenario':str(expense),'retail_credits_scenario':str(credits),
        'retail_usd_scenario':str(credits*Decimal(str(formula['usd_per_credit']))),
        'warning':'Planning scenario, not a measured forecast or maximum. Real graph calls, prompt lengths and compute vary. Server ceiling is the hard provider-spend control.'}


def preflight(api):
    from delivery_api.acceptance_readiness import inspect
    result=inspect(api)
    print(json.dumps({'acceptance_readiness':result['checks'],'activation_enabled':False},indent=2))
    if not result['ready']:
        reasons=[r['reason'] for r in result['checks'] if r['status']=='fail']
        raise Refused('; '.join(reasons))
    pins=result['data']['default pin']
    selected=[p for p in pins.get('pins',[]) if p.get('provider')=='deepseek' and p.get('is_default')]
    ceiling=result['ceiling']
    plan=estimate(selected[0]);plan['server_provider_ceiling_usd']=str(ceiling)
    api('GET','/api/model-routes')
    return selected[0],plan


class Progress:
    """Fixed, names-only status while browser, HTTP or polling work is pending."""
    def __init__(self,write=print,clock=time.monotonic,interval=10):
        self.write=write;self.clock=clock;self.interval=interval;self.started=clock()
        self.stage='Starting acceptance';self.limit=None;self.stage_started=clock()
        self.lock=threading.RLock();self.stop=threading.Event()
    def emit(self):
        with self.lock:
            elapsed=int(self.clock()-self.started);stage_elapsed=int(self.clock()-self.stage_started)
            limit=('; timeout '+str(self.limit)+'s; '+str(max(0,int(self.limit-stage_elapsed)))+'s remaining') if self.limit else '; no additional user input needed'
            self.write('Acceptance: '+self.stage+'; elapsed '+str(elapsed)+'s; stage '+str(stage_elapsed)+'s'+limit,flush=True)
    def set(self,stage,limit=None):
        with self.lock:self.stage=stage;self.limit=limit;self.stage_started=self.clock();self.emit()
    def task(self,tid,base):
        self.write('Acceptance test task: '+tid+'; open '+base+'/tasks/'+tid+'/view. Closing this script does not Stop server work.',flush=True)
    def note(self,message):
        with self.lock:self.write('Acceptance: '+message+'; elapsed '+str(int(self.clock()-self.started))+'s',flush=True)
    def state(self,task):
        mode=task.get('control_snapshot',{}).get('mode')
        if mode in ('working','waiting_input','failed','completed'):
            with self.lock:
                self.stage=self.stage.split('; server state ')[0]+'; server state '+mode
    def beat(self):
        while not self.stop.wait(self.interval):self.emit()
    def __enter__(self):
        self.thread=threading.Thread(target=self.beat,daemon=True);self.thread.start();return self
    def __exit__(self,*args):self.stop.set();self.thread.join(timeout=1)


class QuietProgress:
    def set(self,*args):pass
    def task(self,*args):pass
    def state(self,*args):pass
    def note(self,*args):pass


class API:
    def __init__(self,client):self.client=client;self.records=[]
    def __call__(self,method,path,body=None,*,raw=False,timeout_seconds=90):
        # A normal browser session is cookie-authenticated. Preserve the same
        # Origin proof the browser sends, without weakening the server guard.
        headers={}
        if method.upper() not in ('GET','HEAD','OPTIONS'):
            headers['Origin']=str(self.client.base_url).rstrip('/')
        import httpx
        began=time.monotonic()
        record={'kind':'acceptance.http','method':method,'path':path.split('?')[0],'timeout_seconds':timeout_seconds}
        self.records.append(record);self.records[:]=self.records[-200:]
        try:response=self.client.request(method,path,json=body,timeout=timeout_seconds,headers=headers)
        except httpx.TransportError as cause:
            record.update(outcome='unknown',transport_class=type(cause).__name__,elapsed_seconds=round(time.monotonic()-began,2))
            error=TransportUnavailable('Transport response unavailable; request outcome is unknown')
            error.operation=method+' '+path.split('?')[0];error.timeout_seconds=timeout_seconds
            error.transport_class=type(cause).__name__;error.gate='transport_unknown'
            error.reason=error.operation+' did not return a confirmed response ('+type(cause).__name__+', per-request timeout '+str(timeout_seconds)+'s).'
            error.next_step='Do not repeat a state-changing request. Inspect this task and server evidence; local timeout does not Stop server work.'
            raise error from None
        record.update(status=response.status_code,elapsed_seconds=round(time.monotonic()-began,2))
        if response.status_code>=300:
            # Never echo raw bodies, JWTs, SQL or user content to the terminal.
            error=Refused(f'{method} {path.split("?")[0]} returned HTTP {response.status_code}; review Debug evidence and server prerequisites')
            error.http_status=response.status_code
            error.operation=method+' '+path.split('?')[0]
            error.gate,error.reason,error.next_step=http_blocker(response)
            raise error
        return response.content if raw else response.json()



def http_blocker(response):
    """Recognize fixed public gate responses, never copy arbitrary response text."""
    try:detail=response.json().get('detail')
    except (ValueError,AttributeError):detail=None
    if detail=='Authenticated state changes require a same-origin request.':
        return ('same_origin','Authenticated state changes require a same-origin request.',
                'Use the updated acceptance script; no owner permission or ceiling change is needed.')
    if detail=='Delivery-only runtime: mutations and execution remain fenced':
        return ('execution_runtime','This runtime fences all task mutations.',
                'Start the execution preview and verify the named target before retrying.')
    if detail=='Cloud execution pending scoped worker protocol':
        return ('execution_protocol','The target refuses this execution route.',
                'Check execution readiness on the named target; do not retry blindly.')
    status=response.status_code
    if status==401:return ('authentication','The server rejected the owner session.','Sign in normally again before retrying.')
    if status==403:return ('access_or_origin','The server denied this operation (access or origin check).','Inspect target Debug/server request evidence; do not grant more access blindly.')
    if status==409:return ('task_action','The server refused the test-task state or database prerequisite.','Inspect retained test and server evidence; do not retry blindly.')
    if status==422:return ('request_validation','The server rejected the request shape.','Update the acceptance script to match the server before retrying.')
    return ('http_response','The target returned an unsuccessful HTTP response.','Inspect target Debug/server evidence before retrying.')


def failure_report(error,tid,evidence):
    status=getattr(error,'http_status',None)
    if isinstance(error,KeyboardInterrupt):
        error.reason='Interrupted locally. Server work was not stopped and accepted calls may still bill.'
        error.next_step='Do not start another run. Review the named test in Back office Worker Jobs and confirm Stop there if needed.'
        error.gate='owner_interrupted'
    creation_refused=not tid and getattr(error,'operation',None)=='POST /api/operations/test-tasks' and status in (400,401,403,404,422)
    return {'failed':True,'task_id':tid,'data_retained':True if tid else False if creation_refused else None,
        'task_creation':'confirmed' if tid else 'refused' if creation_refused else 'unconfirmed',
        'paid_start':getattr(error,'paid_start','not_sent' if not tid else 'unconfirmed'),
        'debug_zip':evidence,'error_class':type(error).__name__,
        'operation':getattr(error,'operation',None),'http_status':status,
        'timeout_seconds':getattr(error,'timeout_seconds',None),'transport_class':getattr(error,'transport_class',None),
        'gate':getattr(error,'gate','acceptance_check' if isinstance(error,Refused) else 'unexpected_failure'),
        'reason':getattr(error,'reason',str(error) if isinstance(error,Refused) else 'The acceptance check stopped unexpectedly; private exception details were not printed.'),
        'next':getattr(error,'next_step','Inspect retained test and target Debug/server evidence before retrying. No purge or automatic retry was attempted.')}

def wait_question(api,tid,*,after=None,timeout=900,observe=lambda value:None,progress=None):
    progress=progress or QuietProgress()
    progress.set('Waiting for '+('first' if after is None else 'next')+' text question; polling every 2s',timeout)
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            task=api('GET','/api/tasks/'+tid,timeout_seconds=max(1,min(90,int(deadline-time.monotonic())))) if isinstance(api,API) else api('GET','/api/tasks/'+tid)
        except TransportUnavailable as error:
            # Only this idempotent GET can be repeated. The original question
            # deadline is never extended and no Start/answer is replayed.
            progress.note('Task poll response unavailable; server state unknown; retrying GET only within the original deadline')
            time.sleep(2);continue
        progress.state(task);progress.note('Read-only task poll completed');observe(task)
        control=task.get('control_snapshot',{})
        if task.get('state')=='failed' or control.get('mode')=='failed':raise Refused('The real Start/answer failed; test data retained')
        if control.get('mode')=='waiting_input' and control.get('kind')=='text' and control.get('prompt'):
            if after is None or control.get('input_id')!=after:return task
        if control.get('mode')=='waiting_input' and control.get('kind')!='text':
            raise Refused('The real journey requested a structured decision, not a text question; no blind approval was sent. Test data retained.')
        time.sleep(2)
    raise Refused('No new text question within '+str(timeout)+' seconds; no retry, task retained. Closing the script does not Stop server work.')


def journey(api,*,observe=lambda value:None,progress=None,base=''):
    progress=progress or QuietProgress()
    tid=None;paid_start='not_sent'
    try:
        progress.set('Creating marked test task',90)
        task=api('POST','/api/operations/test-tasks',{'title':'Acceptance DeepSeek '+uuid.uuid4().hex[:12],'brief':IDEA})
        tid=task['task_id']
        progress.task(tid,base)
        if task.get('is_test') is not True:raise Refused('Test marker missing; refusing paid Start')
        progress.set('Submitting paid Start; accepted work may spend under the server ceiling',90)
        paid_start='submitted_outcome_unconfirmed'
        api('POST','/api/tasks/'+tid+'/start',{'model_route':'deepseek'})
        paid_start='accepted'
        first=wait_question(api,tid,observe=observe,progress=progress)
        control=first['control_snapshot']
        progress.set('Submitting the scripted tutor answer; no human answer needed',90)
        api('POST','/api/tasks/'+tid+'/answer',{'answer':ANSWER,'expected_sequence':control.get('precondition',{}).get('session_sequence')})
        second=wait_question(api,tid,after=control.get('input_id'),observe=observe,progress=progress)
        progress.set('Reading settled cost and usage; each HTTP request times out after 90s')
        cost=api('GET','/api/tasks/'+tid+'/cost')
        usage=api('GET','/api/operator/task-usage?task_id='+tid)
        expense=api('GET','/api/operations/test-tasks/'+tid+'/expense')
        usage['provider_expense']=expense
        if not cost.get('evidence',{}).get('complete') or cost.get('price') is None or not usage.get('complete') or usage.get('attempts',0)<1 or not expense.get('complete') or expense.get('provider_usd') is None:
            raise Refused('Actual cost/usage is unsettled or missing; no purge')
        return tid,second,cost,usage
    except (Exception,KeyboardInterrupt) as error:
        if tid:setattr(error,'test_task_id',tid)
        setattr(error,'paid_start',paid_start)
        raise


def save_failure(api,tid,directory,browser=None):
    if not tid:return None
    try:
        raw=api('POST','/api/tasks/'+tid+'/debug.zip',browser or {'records':[]},raw=True)
        if not raw.startswith(b'PK'):raise Refused('Debug response was not a ZIP')
        path=directory/('debug-'+tid+'.zip');path.write_bytes(raw);path.chmod(0o600);return str(path)
    except Exception:return None


def load_settings(root,explicit=None):
    spec=importlib.util.spec_from_file_location('owner_settings',root/'scripts/ops/settings.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module,module.load(root,explicit=explicit)


def target(values,override=None):
    # The preview launcher owns this default. An explicit URL is never silently
    # replaced by another host if it fails. No remote origin is guessed.
    base=(override or values.get('DELIVERY_BASE_URL') or values.get('DELIVERY_PUBLIC_ORIGIN') or
          'http://127.0.0.1:'+str(values.get('PREVIEW_EXEC_PORT') or '8001')).rstrip('/')
    parsed=urlsplit(base)
    if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise Refused('Target must be one app origin, without credentials, path or query; use --base-url')
    local=parsed.hostname in ('localhost','127.0.0.1','::1')
    if not local and parsed.scheme!='https':raise Refused('Cloud target requires HTTPS')
    return base,local


def identify(api,local,*,execute=False):
    state=api('GET','/api/auth')
    if local:
        if state.get('preview') is not True or state.get('local_preview') is not True:
            raise Refused('The local target did not identify as the execution preview. Start scripts/ops/preview-exec.sh and retry; no credentials or paid request sent')
    elif state.get('mode')!='supabase':
        raise Refused('The cloud target did not identify as the signed-in account service; no credentials or paid request sent')
    return state


def authenticate(client,settings,values,base,state):
    """Reuse only owner-supplied private credentials, never fabricate a session."""
    token=values.get('DELIVERY_ACCEPTANCE_TOKEN')
    if token:client.headers['Authorization']='Bearer '+token
    state_path=values.get('DELIVERY_ACCEPTANCE_BROWSER_STATE') or values.get('PREVIEW_BROWSER_STATE') or __import__('os').environ.get('PREVIEW_BROWSER_STATE')
    from delivery_api.owner_browser_session import load_state,sign_in,SessionUnavailable
    try:
        if state_path and not token:load_state(client,state_path,base)
        current=API(client)('GET','/api/auth')
        if not current.get('authenticated'):
            # Never ask a person to extract an HttpOnly browser credential.
            client.headers.pop('Authorization',None)
            sign_in(client,base)
            current=API(client)('GET','/api/auth')
            if not current.get('authenticated'):raise Refused('Owner browser session was not accepted; no acceptance work started')
    except SessionUnavailable as exc:raise Refused(str(exc)) from None
    return current


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=ROOT);p.add_argument('--env-file',type=Path)
    p.add_argument('--dry-run',action='store_true');p.add_argument('--execute',action='store_true')
    p.add_argument('--base-url',help='Explicit app origin; otherwise use the preview settings/default')
    p.add_argument('--output',type=Path,default=Path.home()/'Downloads/requi-acceptance')
    args=p.parse_args(argv)
    if args.execute and args.dry_run:p.error('choose --dry-run or --execute')
    settings,values=load_settings(args.repo,args.env_file)
    base,local=target(values,args.base_url)
    print('Target: '+('local execution preview' if local else 'cloud account service')+' '+base,flush=True)
    import httpx
    started=time.monotonic();stamp=datetime.datetime.now().astimezone().isoformat()
    with httpx.Client(base_url=base,follow_redirects=False,trust_env=False) as client:
        api=API(client)
        state=identify(api,local,execute=args.execute)
        authenticate(client,settings,values,base,state)
        api=API(client);pin,plan=preflight(api)
        print(json.dumps({'at':stamp,'dry_run':not args.execute,'plan':plan},indent=2))
        if not args.execute:
            print('DRY RUN: authenticated reads only; zero task writes, zero provider calls. HTTP content-type regression is covered by tests/test_provider_http.py; this cannot prove the deployed server code version.')
            return 0
        args.output.mkdir(parents=True,exist_ok=True);args.output.chmod(0o700)
        with Progress() as progress:
            tid=None
            browser_evidence={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'url':'','records':[]}
            try:
                from playwright.sync_api import sync_playwright
                live={'seen':False};cost_seen={'seen':False};page_ref={}
                def observe(task):
                    nonlocal tid
                    tid=task['task_id']
                    page=page_ref.get('page')
                    if page and page.url!=base+'/tasks/'+tid+'/view':
                        progress.note('Hidden browser navigating to the test task; navigation timeout 30s')
                        page.goto(base+'/tasks/'+tid+'/view')
                        progress.note('Hidden browser task navigation complete')
                    # Actual persisted child stage data, never synthetic progress.
                    if task.get('control_snapshot',{}).get('mode')=='working' and any(e.get('phase_key')=='intent' and e.get('kind')=='progress' and e.get('payload',{}).get('source')=='interview' for e in task.get('events',[])):
                        if page and page.locator('.phase-list-primary > .phase').first.locator('.phase-current-progress').is_visible():live['seen']=True
                    progress.note('Hidden browser checking visible progress and cost; no clicks or answers are sent by this browser')
                    if page and page.locator('#sessioncost').count() and page.locator('#sessioncost').is_visible() and page.locator('#sessioncost').inner_text().strip():cost_seen['seen']=True
                progress.set('Opening the headless UI verification browser; launch timeout 30s, no human input needed')
                with sync_playwright() as pw:
                    browser=pw.chromium.launch(headless=True)
                    context=browser.new_context(extra_http_headers={'Authorization':client.headers['Authorization']} if client.headers.get('Authorization') else {})
                    context.add_cookies([{'name':c.name,'value':c.value,'url':base} for c in client.cookies.jar])
                    page=context.new_page();page_ref['page']=page
                    def browser_response(response):
                        parsed=urlsplit(response.url)
                        if parsed.netloc==urlsplit(base).netloc:
                            browser_evidence['records'].append({'kind':'hidden_browser.response','method':response.request.method,'path':parsed.path,'status':response.status})
                            browser_evidence['records'][:]=browser_evidence['records'][-300:]
                    page.on('response',browser_response)
                    tid,task,cost,usage=journey(api,observe=observe,progress=progress,base=base)
                    progress.set('Verifying UI; browser operations timeout 30s each, current question timeout 15s')
                    screenshot=args.output/('success-'+tid+'.png');page.screenshot(path=str(screenshot),full_page=True);screenshot.chmod(0o600)
                    if not live['seen'] or not cost_seen['seen']:raise Refused('Live Start progress or visible cost was not proven; test retained')
                    # The actual browser must show the current question before cleanup.
                    from playwright.sync_api import expect
                    progress.note('Hidden browser waiting for the current question text; timeout 15s')
                    expect(page.locator('#currentquestion p')).to_contain_text(task['control_snapshot']['prompt'],timeout=15000)
                    progress.note('Hidden browser current question confirmed; closing verification browser')
                    browser.close()
                progress.set('Purging successful test content; accounting stays retained',90)
                api('POST','/api/operations/test-tasks/'+tid+'/purge',{'confirm':True})
                report={'at':stamp,'model':plan['model'],'pin_revision':pin['revision'],'elapsed_seconds':round(time.monotonic()-started,2),
                    'task_id':tid,'retail_credits':cost['price'],'retail_usd':cost['price_usd'],'provider_usd':usage['provider_expense']['provider_usd'],'usage':usage,
                    'purged':True,'money_and_audit_retained':True}
                result_path=args.output/('result-'+tid+'.json');result_path.write_text(json.dumps(report,indent=2));result_path.chmod(0o600);print(json.dumps(report,indent=2))
            except (Exception,KeyboardInterrupt) as error:
                tid=tid or getattr(error,'test_task_id',None)
                progress.set('Saving retained failure evidence; no Stop or retry is sent',90)
                browser_evidence['ended_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
                browser_evidence['url']=base+'/tasks/'+tid+'/view' if tid else base
                browser_evidence['records']=(browser_evidence['records']+api.records)[-500:]
                evidence=save_failure(api,tid,args.output,browser_evidence)
                report=failure_report(error,tid,evidence)
                print(json.dumps(report),file=sys.stderr)
                operation=report['operation'] or 'acceptance check'
                status=' HTTP '+str(report['http_status']) if report['http_status'] else ''
                raise Refused('Acceptance stopped: '+operation+status+' ['+report['gate']+']: '+report['reason']+' Next: '+report['next']) from None
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except Refused as error:raise SystemExit(str(error)) from None
    except Exception as error:raise SystemExit('Acceptance check failed ('+type(error).__name__+'). Check that the named target is running and your private settings/session are current; no automatic retry.') from None
