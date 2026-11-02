"""Child accounting reads the ledger, never regenerates document projections."""
from delivery_api import app as module
from delivery_api.postgres_scope import RequestStats,request_stats
from test_http_cutover import _failed_needs_task
from test_retail_ledger import account


def test_empty_child_ledger_cost_is_read_only_and_bounded(tmp_path,monkeypatch,pg_scoped_workspace):
    s,tid,_=_failed_needs_task(tmp_path,monkeypatch,pg_scoped_workspace)
    job=s.enqueue_worker(tid,'phase:intent')
    s.retain_decision_review(tid,'child-job:'+job,'child-operation-v1',{'fixture':True})
    stats=RequestStats();token=request_stats.set(stats)
    try:result=module.task_cost(tid,None)
    finally:request_stats.reset(token)
    print('EMPTY_CHILD_COST',stats.queries,stats.transactions,result)
    assert stats.queries<=3
    assert result['price'] is None
    assert result['evidence']['complete'] is False
    assert result['evidence']['scope']=='child ledger'


def test_child_unknown_cost_never_reads_or_writes_legacy_documents(tmp_path,monkeypatch,pg_scoped_workspace):
    s,tid,_=_failed_needs_task(tmp_path,monkeypatch,pg_scoped_workspace)
    job=s.enqueue_worker(tid,'phase:intent')
    s.retain_decision_review(tid,'child-job:'+job,'child-operation-v1',{'fixture':True})
    original=s._execute_native
    def read_only(sql,*args,**kwargs):
        assert not kwargs.get('write')
        assert not any(word in sql.lower() for word in ('generator_documents','document_parts','for update'))
        return original(sql,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(s,'_execute_native',read_only)
        assert module.task_cost(tid,None)['price'] is None
        assert module._task_cost(tid)['price'] is None


def test_child_projection_preserves_posted_partial_and_unknown_outcomes(account,monkeypatch):
    from test_attempt_ledger import A,Q
    from delivery_api.tenancy import tenant_context
    from delivery_api.retail_pricing import RetailRates
    s,tid,job=account
    monkeypatch.setattr(module,'store',s)
    with tenant_context(A):
        s.retain_decision_review(tid,'child-job:'+job,'child-operation-v1',{'fixture':True})
        attempt=s.reserve_provider_attempt(job,job+'-cost',Q,'digest')['attempt_id']
        s.transition_provider_attempt(attempt,'start')
        s.transition_provider_attempt(attempt,'uncertain')
        unknown=module.task_cost(tid,None)
        assert unknown['price'] is None and unknown['evidence']['provider_pending']
        s.post_retail_contribution(key=job+'-partial',job_id=job,phase='intent',kind='tokens',input_tokens=10,rates=RetailRates.from_environment({}).snapshot())
        partial=module.task_cost(tid,None)
        assert partial['price']==.000125 and partial['evidence']['complete'] is False
        # Terminal worker state must not turn an uncertain provider bill into zero/complete.
        s.finish_worker(job)
        assert module.task_cost(tid,None)['evidence']['complete'] is False


def test_child_projection_foreign_owner_cannot_see_totals(account):
    from test_attempt_ledger import A,B
    from delivery_api.tenancy import tenant_context
    s,tid,job=account
    with tenant_context(A):s.retain_decision_review(tid,'child-job:'+job,'child-operation-v1',{'fixture':True})
    import pytest
    with tenant_context(B),pytest.raises(KeyError):s.task_child_cost(tid)


def test_actual_paid_child_first_call_cost_is_unknown_read_only_and_bounded(store,tmp_path,monkeypatch):
    """Real child/graph, parent admission ledger and held fixture HTTP response."""
    import threading,json,uuid,sys
    from pathlib import Path
    from concurrent.futures import ThreadPoolExecutor
    from http.server import BaseHTTPRequestHandler,HTTPServer
    from delivery_api.child_interview import ChildInterviewClient
    from delivery_api.tenancy import tenant_context
    from paid_child_fixture import paid_child
    from test_postgres_store import A,DSN
    from vmodel_contracts.interactive import StartSessionRequest
    entered=threading.Event();release=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['content-length']));entered.set();release.wait(30)
            body=b'{}'
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=HTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with tenant_context(A),paid_child(store,f'http://127.0.0.1:{server.server_port}/v1/chat/completions') as (factory,runtime):
            tid=store.create_task('held child cost '+uuid.uuid4().hex)['task_id'];sid=str(uuid.uuid4());store.set_engine_session(tid,sid)
            client=ChildInterviewClient(store,tmp_path,gateway_factory=factory,paid_runtime=runtime,endpoint=f'http://127.0.0.1:{server.server_port}/v1/chat/completions',
                command=[sys.executable,'-c',"import sys,runpy;sys.path[:0]=sys.argv[1:];runpy.run_module('delivery_api.child_entry',run_name='__main__')",str(Path(__file__).parents[2]/'interview-engine/src'),str(Path(__file__).parents[2]/'spec-generator/src')],dsn=DSN.replace('user=sandbox','user=delivery_app'))
            monkeypatch.setattr(module,'store',store)
            def start():
                with tenant_context(A):return client.start(StartSessionRequest(session_id=sid,task_id=tid,idea='fixture',model_route='deepseek',initial_model='deepseek-flash'))
            with ThreadPoolExecutor(1) as workers:
                future=workers.submit(start)
                try:
                    assert entered.wait(25),'child never reached fixture provider'
                    stats=RequestStats();token=request_stats.set(stats)
                    try:result=module.task_cost(tid,None)
                    finally:request_stats.reset(token)
                    assert result['price'] is None and result['evidence']['provider_pending']
                    assert stats.queries<=3 and stats.transactions==1
                    assert not future.done()
                finally:release.set()
                # Invalid fixture response intentionally fails; retain uncertain evidence.
                try:future.result(timeout=45)
                except Exception:pass
                assert client.supervisor.last_result.reaped
                result=module.task_cost(tid,None)
                assert result['price'] is None and result['evidence']['complete'] is False
    finally:
        release.set();server.shutdown();server.server_close();thread.join(5)


def test_complete_child_cost_and_generator_marker_keep_exact_totals(account,monkeypatch):
    from test_attempt_ledger import A
    from delivery_api.tenancy import tenant_context
    from delivery_api.retail_pricing import RetailRates
    s,tid,job=account;monkeypatch.setattr(module,'store',s)
    with tenant_context(A):
        s.retain_decision_review(tid,'child-generator:'+job,'manifest-v1',{'fixture':True})
        s.post_retail_contribution(key=job+'-complete',job_id=job,phase='intent',kind='tokens',input_tokens=10,rates=RetailRates.from_environment({}).snapshot())
        s.finish_worker(job)
        result=module.task_cost(tid,None)
        assert result['price']==.000125 and result['price_usd']==.000125 and result['evidence']['complete'] is True
