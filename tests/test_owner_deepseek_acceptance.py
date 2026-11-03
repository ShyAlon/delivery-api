"""Hermetic owner script flow; fixture API only, never a real provider."""
import importlib.util
from pathlib import Path
import pytest

from test_owner_test_tasks import operator

spec=importlib.util.spec_from_file_location('acceptance',Path(__file__).parents[1]/'scripts/owner/deepseek-acceptance.py')
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
PIN={'revision':'fixture-revision','provider':'deepseek','is_default':True,'payload':{'row':{'model':'fixture','expense_input':10,'expense_cache_miss':10,'expense_output':20,'expense_units_per_usd':1000,'retail_formula':{'price_per_token':'0.001','price_per_10ms':'0.002','usd_per_credit':'1'}}}}


def api_fixture():
    calls=[];questions=iter(['q1','q2'])
    def api(method,path,body=None,**kw):
        calls.append((method,path,body))
        if path=='/api/auth':return {'authenticated':True,'mode':'supabase','preview':True,'local_preview':True}
        if path=='/api/operations/test-tasks' and method=='GET':return {'tasks':[]}
        if path=='/api/operator/model-pins':return {'pins':[PIN],'expense_policy':{'current':{'limit_units':1000,'units_per_usd':1000}}}
        if path=='/api/model-routes':return {'routes':[]}
        if path=='/api/operations/test-tasks':return {'task_id':'t','is_test':True}
        if path.endswith('/start') or path.endswith('/answer'):return {'status':'starting'}
        if path=='/api/tasks/t':
            key=next(questions);return {'task_id':'t','state':'running','control_snapshot':{'mode':'waiting_input','kind':'text','prompt':'Real fixture question','input_id':key,'sequence':2}}
        if path.endswith('/cost'):return {'price':1,'price_usd':1,'evidence':{'complete':True}}
        if path.endswith('/expense'):return {'provider_usd':'0.02','complete':True}
        if 'task-usage' in path:return {'attempts':2,'complete':True,'settled_expense_groups':[{'expense_units':'20','units_per_usd':'1000'}]}
        raise AssertionError(path)
    return api,calls


def test_dry_preflight_is_only_authenticated_get_and_uses_exact_pin():
    api,calls=api_fixture();pin,plan=a.preflight(api)
    assert all(m=='GET' for m,p,b in calls)
    assert pin==PIN and plan['pin_revision']=='fixture-revision'
    assert plan['provider_usd_scenario']=='320'


def test_backoffice_ceiling_is_the_only_run_limit():
    api,calls=api_fixture();_,plan=a.preflight(api)
    assert plan['server_provider_ceiling_usd']=='1'
    assert all(m=='GET' for m,p,b in calls)


def test_journey_marks_before_start_and_sends_one_text_answer_no_auto_purge():
    api,calls=api_fixture();tid,task,cost,usage=a.journey(api)
    assert tid=='t' and usage['complete']
    writes=[(p,b) for m,p,b in calls if m=='POST']
    assert [p for p,b in writes]==['/api/operations/test-tasks','/api/tasks/t/start','/api/tasks/t/answer']
    assert writes[1][1]=={'model_route':'deepseek'}
    assert writes[2][1]['answer']==a.ANSWER
    assert not any('purge' in p for m,p,b in calls)


def test_missing_marker_stops_before_paid_start():
    api,calls=api_fixture()
    def missing(method,path,body=None,**kw):
        out=api(method,path,body,**kw)
        if method=='POST' and path=='/api/operations/test-tasks':out['is_test']=False
        return out
    with pytest.raises(a.Refused,match='marker'):a.journey(missing)
    assert not any(p.endswith('/start') for m,p,b in calls)


def test_unsettled_cost_retains_test_and_no_purge():
    api,calls=api_fixture()
    def pending(method,path,body=None,**kw):
        out=api(method,path,body,**kw)
        if path.endswith('/cost'):out['evidence']['complete']=False
        return out
    with pytest.raises(a.Refused,match='unsettled'):a.journey(pending)
    assert not any('purge' in p for m,p,b in calls)


def test_structured_decision_is_not_blindly_approved():
    api,calls=api_fixture()
    def choice(method,path,body=None,**kw):
        out=api(method,path,body,**kw)
        if path=='/api/tasks/t':out['control_snapshot']['kind']='approval'
        return out
    with pytest.raises(a.Refused,match='structured decision'):a.journey(choice)
    assert not any(p.endswith('/answer') for m,p,b in calls)


def test_main_dry_run_has_zero_posts_or_browser_or_provider(tmp_path,monkeypatch,capsys):
    import httpx
    settings=type('Settings',(),{})()
    monkeypatch.setattr(a,'load_settings',lambda *args:(settings,{'DELIVERY_BASE_URL':'https://fixture.invalid','DELIVERY_ACCEPTANCE_TOKEN':'private-token'}))
    api,calls=api_fixture()
    class Client:
        def __init__(self,**kw):self.headers={}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def request(self,method,path,**kw):
            return httpx.Response(200,json=api(method,path,kw.get('json')))
    monkeypatch.setattr(httpx,'Client',Client)
    assert a.main(['--dry-run'])==0
    assert all(m=='GET' for m,p,b in calls)
    assert not list(tmp_path.iterdir())
    assert 'private-token' not in capsys.readouterr().out


def test_owner_expense_read_uses_actual_scale_and_refuses_non_test(store,operator):
    from test_owner_test_tasks import completed_test
    from test_postgres_store import A,DSN
    from delivery_api.tenancy import tenant_context
    import psycopg,uuid
    t,sid=completed_test(store)
    with tenant_context(A):
        assert store.test_task_expense(t['task_id'])['complete'] is False
        real=store.create_task('real-expense-'+uuid.uuid4().hex)
        with pytest.raises(KeyError):store.test_task_expense(real['task_id'])
    with psycopg.connect(DSN) as c:
        job=c.execute('SELECT job_id FROM worker_jobs WHERE task_id=%s LIMIT 1',(t['task_id'],)).fetchone()[0]
        c.execute("INSERT INTO provider_attempts(attempt_id,owner_id,job_id,config_version,quote,fingerprint,state,expense_reserved,retail_reserved,expense_actual,retail_actual,created_at,updated_at) VALUES(%s,%s,%s,'test','{\"expense_units_per_usd\":1000}','test','settled',1,1,25,1,1,2)",('exp-'+uuid.uuid4().hex,A.owner_id,job))
    with tenant_context(A):
        assert store.test_task_expense(t['task_id'])=={'provider_usd':'0.025','complete':True,'attempts':1,'test':True}


def test_target_default_and_override_never_fall_back():
    assert a.target({})==('http://127.0.0.1:8001',True)
    assert a.target({'PREVIEW_EXEC_PORT':'8123'})==('http://127.0.0.1:8123',True)
    assert a.target({'DELIVERY_PUBLIC_ORIGIN':'https://fixture.invalid'})==('https://fixture.invalid',False)
    assert a.target({},'http://localhost:8999')==('http://localhost:8999',True)
    for url in ('https://user:secret@fixture.invalid','http://cloud.invalid','https://fixture.invalid/tasks'):
        with pytest.raises(a.Refused):a.target({},url)


def test_wrong_target_refuses_before_auth_prompt_or_writes():
    calls=[]
    def api(method,path):calls.append((method,path));return {'authenticated':False,'mode':'supabase'}
    with pytest.raises(a.Refused,match='identify'):a.identify(api,True,execute=True)
    assert calls==[('GET','/api/auth')]


def test_missing_session_opens_browser_once_never_prompts_for_token(monkeypatch):
    import httpx
    from delivery_api import owner_browser_session
    calls=[];opened=[]
    class Settings:
        def hidden_prompt(self,name):pytest.fail('never ask for a browser token')
    def signin(client,base):opened.append(base);client.cookies.set('requi_access','private-cookie')
    monkeypatch.setattr(owner_browser_session,'sign_in',signin)
    def handle(request):
        calls.append(request.method)
        return httpx.Response(200,json={'authenticated':'requi_access=private-cookie' in request.headers.get('cookie','')})
    with httpx.Client(base_url='http://127.0.0.1:8001',transport=httpx.MockTransport(handle)) as client:
        assert a.authenticate(client,Settings(),{},'http://127.0.0.1:8001',{})['authenticated']
        assert a.authenticate(client,Settings(),{},'http://127.0.0.1:8001',{})['authenticated']
    assert opened==['http://127.0.0.1:8001'] and set(calls)=={'GET'}


def test_shared_readiness_reports_each_gap_without_raw_private_values():
    from delivery_api.acceptance_readiness import inspect
    def unavailable(*args):raise RuntimeError('private-dsn-secret')
    report=inspect(unavailable)
    assert not report['ready'] and not report['activation_enabled']
    assert {r['check'] for r in report['checks']}=={'session','target','token/session','marker migration','default pin','ceiling'}
    assert all(r['reason'] for r in report['checks'])
    assert 'private-dsn-secret' not in str(report)


def test_shared_readiness_distinguishes_owner_permission_and_orders_next_actions():
    from delivery_api.acceptance_readiness import inspect
    api,calls=api_fixture()
    def denied(method,path):
        if path=='/api/operations/test-tasks':
            error=a.Refused('forbidden');error.http_status=403;raise error
        return api(method,path)
    result=inspect(denied)
    row=next(r for r in result['checks'] if r['check']=='marker migration')
    assert 'lacks reviewed owner-test access' in row['reason']
    assert '--test-owner' in row['next'] and not result['ready']
    assert [r['step'] for r in result['checks']]==sorted(r['step'] for r in result['checks'])


def test_no_duplicate_cli_limit_is_accepted():
    with pytest.raises(SystemExit):a.main(['--dry-run','--max-usd','4'])


def test_cookie_write_uses_target_origin_and_real_csrf_guard(monkeypatch):
    import httpx
    from fastapi import FastAPI,Request
    from fastapi.testclient import TestClient
    from delivery_api import auth
    monkeypatch.setattr(auth,'enabled',lambda:True)
    monkeypatch.setattr(auth.config,'PUBLIC_ORIGIN','http://testserver')
    monkeypatch.setattr(auth.config,'ALT_ORIGINS',[])
    app=FastAPI();created=[]
    @app.get('/api/auth')
    def read():return {'authenticated':True}
    @app.post('/api/operations/test-tasks')
    def create(request:Request):
        auth.csrf_guard(request);created.append(True)
        return {'task_id':'fixture','is_test':True}
    with TestClient(app) as client:
        client.cookies.set('requi_access','private-cookie')
        assert client.get('/api/auth').status_code==200
        denied=client.post('/api/operations/test-tasks',json={})
        assert denied.status_code==403 and not created
        assert a.API(client)('POST','/api/operations/test-tasks',{})['is_test']
        assert created==[True]


def test_failure_keeps_safe_http_blocker_and_no_raw_body(capsys):
    import httpx
    def handle(request):
        return httpx.Response(403,json={'detail':'Authenticated state changes require a same-origin request.','secret':'private-token'})
    with httpx.Client(base_url='http://fixture.invalid',transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(a.Refused) as caught:a.API(client)('POST','/api/operations/test-tasks',{})
    report=a.failure_report(caught.value,None,None)
    assert report['http_status']==403
    assert report['gate']=='same_origin'
    assert 'same-origin' in report['reason']
    assert report['operation']=='POST /api/operations/test-tasks'
    assert report['task_creation']=='refused' and report['paid_start']=='not_sent'
    assert 'private-token' not in str(report)
    assert 'HTTP 403' in str(caught.value)


def test_unknown_failure_never_echoes_private_error_or_claims_no_writes():
    report=a.failure_report(RuntimeError('private-dsn-secret'),None,None)
    assert 'private-dsn-secret' not in str(report)
    assert report['task_creation']=='unconfirmed' and report['paid_start']=='not_sent'
    assert report['data_retained'] is None
    assert report['reason'] and report['next']


def test_journey_create_refusal_never_sends_start_and_preserves_http_evidence():
    import httpx
    calls=[]
    def handle(request):
        calls.append((request.method,request.url.path))
        return httpx.Response(403,json={'detail':'Authenticated state changes require a same-origin request.'})
    with httpx.Client(base_url='http://fixture.invalid',transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(a.Refused) as caught:a.journey(a.API(client))
    report=a.failure_report(caught.value,None,None)
    assert report['http_status']==403 and report['paid_start']=='not_sent'
    assert calls==[('POST','/api/operations/test-tasks')]


@pytest.mark.parametrize('status,gate',[(401,'authentication'),(403,'access_or_origin'),(409,'task_action'),(422,'request_validation'),(500,'http_response')])
def test_unrecognized_server_content_is_not_echoed(status,gate):
    import httpx
    response=httpx.Response(status,json={'detail':{'error':'private-user-content','cause':'postgres://private-secret'}})
    result=a.http_blocker(response)
    assert result[0]==gate and 'private' not in str(result)


def test_ambiguous_creation_failure_is_not_reported_as_no_task():
    error=a.Refused('unknown outcome');error.http_status=500;error.operation='POST /api/operations/test-tasks'
    assert a.failure_report(error,None,None)['task_creation']=='unconfirmed'


def test_final_abort_line_repeats_observed_status_gate_and_reason(monkeypatch,capsys,tmp_path):
    import httpx
    monkeypatch.setattr(a,'load_settings',lambda *args:(object(),{'DELIVERY_BASE_URL':'http://127.0.0.1:8001'}))
    monkeypatch.setattr(a,'identify',lambda *args,**kwargs:{})
    monkeypatch.setattr(a,'authenticate',lambda *args:{})
    monkeypatch.setattr(a,'preflight',lambda *args:(PIN,{'model':'fixture'}))
    def refused(*args,**kwargs):
        error=a.Refused('HTTP refusal');error.http_status=403;error.operation='POST /api/operations/test-tasks'
        error.gate='same_origin';error.reason='Authenticated state changes require a same-origin request.'
        error.next_step='Use the updated acceptance script.';error.paid_start='not_sent';raise error
    monkeypatch.setattr(a,'journey',refused)
    with pytest.raises(a.Refused,match='HTTP 403 \\[same_origin\\]') as caught:
        a.main(['--execute','--output',str(tmp_path)])
    assert 'same-origin request' in str(caught.value)
    output=capsys.readouterr().err
    assert '"http_status": 403' in output and '"paid_start": "not_sent"' in output
    assert '"task_creation": "refused"' in output


def test_progress_reports_stage_elapsed_timeout_and_task_link_without_content(capsys):
    clock=[10]
    progress=a.Progress(clock=lambda:clock[0])
    progress.set('Waiting for first text question',900)
    clock[0]=25;progress.emit()
    progress.task('fixture-task','http://127.0.0.1:8001')
    out=capsys.readouterr().out
    assert 'elapsed 15s; stage 15s; timeout 900s; 885s remaining' in out
    assert 'http://127.0.0.1:8001/tasks/fixture-task/view' in out
    assert 'does not Stop server work' in out


def test_heartbeat_continues_during_a_blocking_step():
    import time
    rows=[]
    with a.Progress(write=lambda line,**kwargs:rows.append(line),interval=.01) as progress:
        progress.set('Opening browser',30);time.sleep(.035)
    assert len(rows)>=3 and not progress.thread.is_alive()


def test_question_timeout_is_explicit_and_never_retries_or_submits(monkeypatch):
    clock=iter([0,1,3]);monkeypatch.setattr(a.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(a.time,'sleep',lambda n:None)
    calls=[]
    def api(method,path):calls.append((method,path));return {'task_id':'t','control_snapshot':{'mode':'working'}}
    with pytest.raises(a.Refused,match='within 2 seconds'):a.wait_question(api,'t',timeout=2)
    assert calls==[('GET','/api/tasks/t')]


def test_interrupt_retains_task_id_and_warns_server_was_not_stopped():
    api,calls=api_fixture()
    def interrupt(method,path,body=None,**kwargs):
        if method=='GET' and path=='/api/tasks/t':raise KeyboardInterrupt()
        return api(method,path,body,**kwargs)
    with pytest.raises(KeyboardInterrupt) as caught:a.journey(interrupt)
    report=a.failure_report(caught.value,caught.value.test_task_id,None)
    assert report['task_id']=='t' and report['paid_start']=='accepted'
    assert report['gate']=='owner_interrupted' and 'not stopped' in report['reason']
    assert 'confirm Stop' in report['next']
    assert not any('purge' in p or p.endswith('/stop') for m,p,b in calls)


def test_poll_read_timeout_is_unknown_then_retried_without_repeating_mutation(monkeypatch):
    import httpx
    calls=[];reads=[0]
    def handle(request):
        calls.append((request.method,request.url.path))
        reads[0]+=1
        if reads[0]==1:raise httpx.ReadTimeout('private-host-secret')
        return httpx.Response(200,json={'task_id':'t','control_snapshot':{'mode':'waiting_input','kind':'text','prompt':'fixture','input_id':'q'}})
    monkeypatch.setattr(a.time,'sleep',lambda n:None)
    with httpx.Client(base_url='http://fixture.invalid',transport=httpx.MockTransport(handle)) as client:
        assert a.wait_question(a.API(client),'t',timeout=10)['task_id']=='t'
    assert calls==[('GET','/api/tasks/t')]*2


def test_answer_transport_timeout_names_operation_and_never_replays():
    import httpx
    calls=[]
    def handle(request):
        calls.append(request.method)
        raise httpx.ReadTimeout('private-host-token')
    with httpx.Client(base_url='http://fixture.invalid',transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(a.TransportUnavailable) as caught:a.API(client)('POST','/api/tasks/t/answer',{})
    report=a.failure_report(caught.value,'t',None)
    assert report['operation']=='POST /api/tasks/t/answer' and report['timeout_seconds']==90
    assert report['transport_class']=='ReadTimeout' and report['gate']=='transport_unknown'
    assert 'private-host-token' not in str(report) and calls==['POST']


def test_transport_retries_do_not_extend_question_deadline(monkeypatch):
    import httpx
    clock=[0];calls=[]
    monkeypatch.setattr(a.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(a.time,'sleep',lambda n:clock.__setitem__(0,clock[0]+n))
    def handle(request):calls.append(request.method);raise httpx.ReadTimeout('private')
    with httpx.Client(base_url='http://fixture.invalid',transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(a.Refused,match='within 3 seconds'):a.wait_question(a.API(client),'t',timeout=3)
    assert calls==['GET','GET'] and clock[0]==4


def test_failure_zip_receives_browser_and_operation_evidence(tmp_path):
    calls=[]
    evidence={'started_at':'fixture','url':'http://fixture/tasks/t/view','records':[{'kind':'acceptance.http','method':'POST','path':'/api/tasks/t/answer','outcome':'unknown','transport_class':'ReadTimeout'}]}
    def api(method,path,body,**kwargs):calls.append((method,path,body));return b'PKfixture'
    assert a.save_failure(api,'t',tmp_path,evidence)
    assert calls[0][2]==evidence
