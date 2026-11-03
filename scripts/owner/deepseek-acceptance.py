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
    state=api('GET','/api/auth')
    if not state.get('authenticated'):raise Refused('Sign-in is missing or expired; sign in again')
    view=api('GET','/api/operations/test-tasks')
    if not isinstance(view.get('tasks'),list):raise Refused('Owner test migration/permission is not ready')
    pins=api('GET','/api/operator/model-pins')
    selected=[p for p in pins.get('pins',[]) if p.get('provider')=='deepseek' and p.get('is_default')]
    if len(selected)!=1:raise Refused('Exactly one default reviewed DeepSeek pin is required; review it in Back office')
    policy=pins.get('expense_policy',{}).get('current')
    if not policy:raise Refused('Set the operator expense ceiling in Back office before running')
    ceiling=Decimal(policy['limit_units'])/positive(policy['units_per_usd'])
    if maximum is not None and ceiling>maximum:
        raise Refused('The server task-generation ceiling exceeds --max-usd; lower it in Back office before running. This script will not edit it.')
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


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=ROOT);p.add_argument('--env-file',type=Path)
    p.add_argument('--dry-run',action='store_true');p.add_argument('--execute',action='store_true')
    p.add_argument('--max-usd',type=positive);p.add_argument('--output',type=Path,default=Path.home()/'Downloads/requi-acceptance')
    args=p.parse_args(argv)
    if args.execute and args.dry_run:p.error('choose --dry-run or --execute')
    if args.execute and args.max_usd is None:p.error('--execute requires an explicitly approved --max-usd')
    settings,values=load_settings(args.repo,args.env_file)
    base=values.get('DELIVERY_BASE_URL','').rstrip('/')
    parsed=urlsplit(base)
    if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise Refused('Set DELIVERY_BASE_URL to the real app origin in the private settings file')
    if parsed.scheme=='http' and parsed.hostname not in ('localhost','127.0.0.1','::1'):raise Refused('Remote sign-in requires HTTPS')
    import httpx
    started=time.monotonic();stamp=datetime.datetime.now().astimezone().isoformat()
    with httpx.Client(base_url=base,follow_redirects=False,trust_env=False) as client:
        token=values.get('DELIVERY_ACCEPTANCE_TOKEN')
        if token:client.headers['Authorization']='Bearer '+token
        else:
            if not args.execute:raise Refused('Dry-run needs DELIVERY_ACCEPTANCE_TOKEN in private settings; it never logs in or changes data')
            settings.require(values,['DELIVERY_ACCEPTANCE_EMAIL','DELIVERY_ACCEPTANCE_PASSWORD'])
            API(client)('POST','/auth/signin',{'username':values['DELIVERY_ACCEPTANCE_EMAIL'],'password':values['DELIVERY_ACCEPTANCE_PASSWORD']})
        api=API(client);pin,plan=preflight(api,args.max_usd if args.execute else None)
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
