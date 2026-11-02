import json
from types import SimpleNamespace
from delivery_api.debug_collection import collect


def fake_module():
    calls=[]
    module=SimpleNamespace(config=SimpleNamespace(CLOUD_TENANTS=True),app=SimpleNamespace(state=SimpleNamespace()),store=None)
    def bind(name):
        def run(*args):calls.append((name,args));return {}
        setattr(module,name,run)
    for name in ['account_ledger_view','account_credit_history','debug_preflight','admin_operations_request_costs','admin_operations_capacity','debug_log_durability','debug_evidence','operator_task_debug','operator_task_logs','operator_task_failure_diagnostics','operator_task_usage']:bind(name)
    return module,calls


def test_customer_batch_never_calls_operator_sources():
    module,calls=fake_module();actor=SimpleNamespace(admin=False)
    result=collect(module,actor,'a')
    assert [x[0] for x in calls]==['account_ledger_view','account_credit_history','debug_preflight']
    assert len(result['sections'])==3


def test_operator_batch_exact_task_failures_and_safe_unavailability(monkeypatch):
    from delivery_api import cloud_execution,cloud_runtime
    monkeypatch.setattr(cloud_execution,'problems',lambda:['Choose one reviewed deepseek model in Back office > Model pins'])
    monkeypatch.setattr(cloud_runtime,'installed',lambda:False)
    module,calls=fake_module();actor=SimpleNamespace(admin=True)
    module.operator_task_logs=lambda *args:(_ for _ in ()).throw(ValueError('private backend error'))
    module.operator_task_usage=lambda *args:{'availability':'unavailable','required_capability':'usage.read','reason':'Usage is not configured'}
    module.account_credit_history=lambda *args:{'text':'credit history evidence'}
    result=collect(module,actor,'task a');sections={s['source']:s for s in result['sections']}
    assert len(sections)==12
    assert sections['execution-readiness']['data']['installed'] is False
    assert 'Model pins' in sections['execution-readiness']['data']['reasons'][0]
    assert sections['db-state-events']['availability']=='unavailable'
    assert sections['credit-history']['data']['text']=='credit history evidence'
    assert sections['db-usage']['availability']=='unavailable' and sections['db-usage']['data']['required_capability']=='usage.read'
    assert ('operator_task_failure_diagnostics',('task a',20,actor)) in calls
    assert ('debug_evidence',('task a',actor)) in calls
    assert 'private backend error' not in json.dumps(result)
    assert all(s['duration_ms']>=0 for s in result['sections'])


def test_server_log_task_filter_never_includes_neighboring_task(monkeypatch,file_log_evidence,pg_operator_log_reader):
 from delivery_api import app as m
 from types import SimpleNamespace
 reader,store=pg_operator_log_reader
 monkeypatch.setattr(m,'store',store)
 monkeypatch.setattr(m,'_operator_reader',lambda:reader)
 repo=file_log_evidence.repository
 repo.append('activity','INFO task=task-a own',task_id='task-a')
 repo.append('activity','INFO task=task-a-other neighbor',task_id='task-a-other')
 repo.append('activity','INFO system-wide')
 from delivery_api.process_logs import flush
 assert flush(5)
 got=m.server_log(30,SimpleNamespace(id='operator',admin=True),'task-a')
 assert 'INFO task=task-a own' in got['text'] and got['count']==1
 assert 'neighbor' not in got['text'] and 'system-wide' not in got['text']
