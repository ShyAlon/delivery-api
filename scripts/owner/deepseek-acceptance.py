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
from urllib.parse import urlsplit
import uuid

ROOT=Path(__file__).resolve().parents[2]
IDEA='A simple web app for a solo tutor to manage students and lesson bookings. Each booking has a student, a date and time, and a short note. No payments, AI features, integrations or public signup.'
ANSWER='I am the only tutor and user. I create student records with a name and email, then book one-hour lessons in my local timezone. I need to see upcoming bookings and edit or cancel them. A double booking should be rejected. No payments, messaging or external calendars are needed.'

class Refused(RuntimeError):pass


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


def preflight(api,maximum=None):
    from delivery_api.acceptance_readiness import inspect
    result=inspect(api,maximum)
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


class API:
    def __init__(self,client):self.client=client
    def __call__(self,method,path,body=None,*,raw=False):
        response=self.client.request(method,path,json=body,timeout=90)
        if response.status_code>=300:
            # Never echo raw bodies, JWTs, SQL or user content to the terminal.
            raise Refused(f'{method} {path.split("?")[0]} returned HTTP {response.status_code}; review Debug evidence and server prerequisites')
        return response.content if raw else response.json()


def wait_question(api,tid,*,after=None,timeout=900,observe=lambda value:None):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        task=api('GET','/api/tasks/'+tid);observe(task)
        control=task.get('control_snapshot',{})
        if task.get('state')=='failed' or control.get('mode')=='failed':raise Refused('The real Start/answer failed; test data retained')
        if control.get('mode')=='waiting_input' and control.get('kind')=='text' and control.get('prompt'):
            if after is None or control.get('input_id')!=after:return task
        if control.get('mode')=='waiting_input' and control.get('kind')!='text':
            raise Refused('The real journey requested a structured decision, not a text question; no blind approval was sent. Test data retained.')
        time.sleep(2)
    raise Refused('No new text question before the deadline; no retry, data retained')


def journey(api,*,observe=lambda value:None):
    tid=None
    try:
        task=api('POST','/api/operations/test-tasks',{'title':'Acceptance DeepSeek '+uuid.uuid4().hex[:12],'brief':IDEA})
        tid=task['task_id']
        if task.get('is_test') is not True:raise Refused('Test marker missing; refusing paid Start')
        api('POST','/api/tasks/'+tid+'/start',{'model_route':'deepseek'})
        first=wait_question(api,tid,observe=observe)
        control=first['control_snapshot']
        api('POST','/api/tasks/'+tid+'/answer',{'answer':ANSWER,'expected_sequence':control.get('precondition',{}).get('session_sequence')})
        second=wait_question(api,tid,after=control.get('input_id'),observe=observe)
        cost=api('GET','/api/tasks/'+tid+'/cost')
        usage=api('GET','/api/operator/task-usage?task_id='+tid)
        expense=api('GET','/api/operations/test-tasks/'+tid+'/expense')
        usage['provider_expense']=expense
        if not cost.get('evidence',{}).get('complete') or cost.get('price') is None or not usage.get('complete') or usage.get('attempts',0)<1 or not expense.get('complete') or expense.get('provider_usd') is None:
            raise Refused('Actual cost/usage is unsettled or missing; no purge')
        return tid,second,cost,usage
    except Exception as error:
        if tid:setattr(error,'test_task_id',tid)
        raise


def save_failure(api,tid,directory):
    if not tid:return None
    try:
        raw=api('POST','/api/tasks/'+tid+'/debug.zip',{'records':[]},raw=True)
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
    if state_path and not token:
        path=Path(state_path).expanduser()
        if path.is_symlink() or not path.is_file() or path.stat().st_mode&0o077:
            raise Refused('Browser session file must exist, be private (chmod 600), and not be a symlink')
        try:
            cookies=json.loads(path.read_text()).get('cookies',[])
            host=urlsplit(base).hostname
            for cookie in cookies:
                # Exact origin host only. Never copy another site's cookies or
                # localStorage tokens to this target.
                if cookie.get('domain','').lstrip('.')==host and cookie.get('name') in ('requi_access','requi_refresh','requi_session'):
                    client.cookies.set(cookie['name'],cookie['value'],domain=host,path=cookie.get('path','/'))
        except (ValueError,KeyError,TypeError):raise Refused('Saved browser session could not be read; refresh it and retry') from None
    current=API(client)('GET','/api/auth')
    if not current.get('authenticated'):
        # A dry run never creates a sign-in session. Collect only a missing
        # existing access token through the shared hidden terminal prompt.
        values['DELIVERY_ACCEPTANCE_TOKEN']=settings.hidden_prompt('Existing signed-in owner access token (hidden; not saved)')
        if not values['DELIVERY_ACCEPTANCE_TOKEN']:raise Refused('Owner access token missing; nothing changed')
        client.headers['Authorization']='Bearer '+values['DELIVERY_ACCEPTANCE_TOKEN']
        current=API(client)('GET','/api/auth')
        if not current.get('authenticated'):raise Refused('Owner session is expired or not accepted; sign in to the preview and retry')
    return current


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=ROOT);p.add_argument('--env-file',type=Path)
    p.add_argument('--dry-run',action='store_true');p.add_argument('--execute',action='store_true')
    p.add_argument('--base-url',help='Explicit app origin; otherwise use the preview settings/default')
    p.add_argument('--max-usd',type=positive);p.add_argument('--output',type=Path,default=Path.home()/'Downloads/requi-acceptance')
    args=p.parse_args(argv)
    if args.execute and args.dry_run:p.error('choose --dry-run or --execute')
    if args.execute and args.max_usd is None:p.error('--execute requires an explicitly approved --max-usd')
    settings,values=load_settings(args.repo,args.env_file)
    base,local=target(values,args.base_url)
    print('Target: '+('local execution preview' if local else 'cloud account service')+' '+base,flush=True)
    import httpx
    started=time.monotonic();stamp=datetime.datetime.now().astimezone().isoformat()
    with httpx.Client(base_url=base,follow_redirects=False,trust_env=False) as client:
        api=API(client)
        state=identify(api,local,execute=args.execute)
        authenticate(client,settings,values,base,state)
        api=API(client);pin,plan=preflight(api,args.max_usd)
        print(json.dumps({'at':stamp,'dry_run':not args.execute,'plan':plan},indent=2))
        if not args.execute:
            print('DRY RUN: authenticated reads only; zero task writes, zero provider calls. HTTP content-type regression is covered by tests/test_provider_http.py; this cannot prove the deployed server code version.')
            return 0
        args.output.mkdir(parents=True,exist_ok=True);args.output.chmod(0o700)
        tid=None
        try:
            from playwright.sync_api import sync_playwright
            live={'seen':False};cost_seen={'seen':False};page_ref={}
            def observe(task):
                nonlocal tid
                tid=task['task_id']
                page=page_ref.get('page')
                if page and page.url!=base+'/tasks/'+tid+'/view':page.goto(base+'/tasks/'+tid+'/view')
                # Actual persisted child stage data, never synthetic progress.
                if task.get('control_snapshot',{}).get('mode')=='working' and any(e.get('phase_key')=='intent' and e.get('kind')=='progress' and e.get('payload',{}).get('source')=='interview' for e in task.get('events',[])):
                    if page and page.locator('.phase-list-primary > .phase').first.locator('.phase-current-progress').is_visible():live['seen']=True
                if page and page.locator('#sessioncost').count() and page.locator('#sessioncost').is_visible() and page.locator('#sessioncost').inner_text().strip():cost_seen['seen']=True
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                context=browser.new_context(extra_http_headers={'Authorization':client.headers['Authorization']} if client.headers.get('Authorization') else {})
                context.add_cookies([{'name':c.name,'value':c.value,'url':base} for c in client.cookies.jar])
                page=context.new_page();page_ref['page']=page
                tid,task,cost,usage=journey(api,observe=observe)
                screenshot=args.output/('success-'+tid+'.png');page.screenshot(path=str(screenshot),full_page=True);screenshot.chmod(0o600)
                if not live['seen'] or not cost_seen['seen']:raise Refused('Live Start progress or visible cost was not proven; test retained')
                # The actual browser must show the current question before cleanup.
                from playwright.sync_api import expect
                expect(page.locator('#currentquestion p')).to_contain_text(task['control_snapshot']['prompt'],timeout=15000)
                browser.close()
            api('POST','/api/operations/test-tasks/'+tid+'/purge',{'confirm':True})
            report={'at':stamp,'model':plan['model'],'pin_revision':pin['revision'],'elapsed_seconds':round(time.monotonic()-started,2),
                'task_id':tid,'retail_credits':cost['price'],'retail_usd':cost['price_usd'],'provider_usd':usage['provider_expense']['provider_usd'],'usage':usage,
                'purged':True,'money_and_audit_retained':True}
            result_path=args.output/('result-'+tid+'.json');result_path.write_text(json.dumps(report,indent=2));result_path.chmod(0o600);print(json.dumps(report,indent=2))
        except Exception as error:
            tid=tid or getattr(error,'test_task_id',None)
            evidence=save_failure(api,tid,args.output)
            print(json.dumps({'failed':True,'task_id':tid,'data_retained':True,'debug_zip':evidence,'error_class':type(error).__name__,'next':'Inspect retained test and Debug evidence. Do not retry blindly.'}),file=sys.stderr)
            raise Refused('Acceptance failed; test data retained, no purge and no automatic retry') from None
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except Refused as error:raise SystemExit(str(error)) from None
    except Exception as error:raise SystemExit('Acceptance check failed ('+type(error).__name__+'). Check that the named target is running and your private settings/session are current; no automatic retry.') from None
