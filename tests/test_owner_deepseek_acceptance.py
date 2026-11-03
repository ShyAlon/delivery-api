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
        if path=='/api/auth':return {'authenticated':True}
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


def test_server_ceiling_must_not_exceed_explicit_owner_approval():
    from decimal import Decimal
    api,calls=api_fixture()
    with pytest.raises(a.Refused,match='server task-generation ceiling'):a.preflight(api,Decimal('0.5'))
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
