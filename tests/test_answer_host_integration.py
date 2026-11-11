"""Explicitly injected inactive answer mode: real graph, owned fake HTTP/PG."""
import json,sys,threading,uuid,os
import pytest
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from delivery_api.answer_runtime import AnswerAccountingMode,AnswerReceiptStore
from delivery_api.child_paid_runtime import PaidChildRuntime
from delivery_api.child_interview import ChildInterviewClient,ChildInterviewHost,LocalComputeEvidence
from delivery_api.retail_pricing import RetailRates
from delivery_api.tenancy import tenant_context


@pytest.mark.parametrize('outcome',['success','missing-usage','stop','http-error'])
def test_real_paid_answer_uses_one_start_finish_without_per_call_ledger(pg_workspace_factory,tmp_path,monkeypatch,outcome):
    calls=[];legacy=[];payments=[];answer_phase=[False]
    entered=threading.Event();release=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            from strict_provider import read_request
            payload=read_request(self);calls.append(payload)
            if answer_phase[0] and outcome=='stop':
                entered.set();assert release.wait(15)
            if answer_phase[0] and outcome=='http-error':
                body=b'{"error":{"code":"invalid_request","message":"fake refusal"}}'
                self.send_response(400);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body);return
            content={'is_web_app':True,'archetype':'saas_crud','confidence':0.9,'reason':'fake web app'} if 'is_web_app' in json.dumps(payload) else {'question':'What does a booking look like for your customers?','covered':[],'thin':[],'skipped':[],'complete':False}
            message={'role':'assistant','content':json.dumps(content)}
            if payload.get('tool_choice'):
                message={'role':'assistant','content':None,'tool_calls':[{'id':'fake-tool','type':'function','function':{'name':payload['tool_choice']['function']['name'],'arguments':json.dumps({'payload':content})}}]}
            body=json.dumps({'id':'fake','object':'chat.completion','model':payload['model'],
                'choices':[{'index':0,'message':message,'finish_reason':'stop'}],
                'usage':{'prompt_tokens':10,'completion_tokens':1,'total_tokens':11,'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':10}}).encode()
            if answer_phase[0] and outcome=='missing-usage':
                value=json.loads(body);value.pop('usage');body=json.dumps(value).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
        def log_message(self,*a):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    endpoint=f'http://127.0.0.1:{server.server_port}/v1/chat/completions'
    class Runtime(PaidChildRuntime):
        def transport(self,*a):return endpoint,{}
        def compute(self,*a):return LocalComputeEvidence()  # Start only, fake legacy seam.
    class Gateway:
        retail_rates=RetailRates('0.0000125','0.0000005','1')
        def __init__(self,job):self.job_id=job;self.states={}
        def reserve_serialized(self,**kw):legacy.append('reserve');self.states['fake']='reserved';return 'fake'
        def start(self,a):legacy.append('start');self.states[a]='started'
        def settle_raw(self,a,u):legacy.append('settle');self.states[a]='settled'
        def uncertain(self,a):legacy.append('unknown');self.states[a]='uncertain'
        def _attempt(self,a):return {'state':self.states[a]}
    client=None
    try:
        with pg_workspace_factory() as w,tenant_context(w.scope):
            with w.admin() as c:c.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,1000000000,1000000000,0)',(w.scope.owner_id,))
            tid=w.store.create_task('two-touch real graph')['task_id'];sid=str(uuid.uuid4());w.store.set_engine_session(tid,sid)
            command=[sys.executable,'-c',"import sys,runpy;sys.path[:0]=sys.argv[1:];runpy.run_module('delivery_api.child_entry',run_name='__main__')",str(Path(__file__).parents[1]/'src'),str(Path(os.environ['DELIVERY_TEST_ENGINE_SOURCE'])/'src') if os.getenv('DELIVERY_TEST_ENGINE_SOURCE') else str(Path(__import__('interview_engine').__file__).parents[1])]
            from delivery_api.answer_mode import from_environment
            runtime=Runtime(models={'deepseek':'deepseek-flash'},api_key='fake-parent-only')
            client=ChildInterviewHost(w.store,tmp_path,gateway_factory=lambda cap,job:Gateway(job),
                session_dsn=w.dsn.replace('user=sandbox','user=delivery_app'),endpoint=endpoint,
                paid_runtime=runtime,answer_accounting=from_environment({'DELIVERY_ANSWER_ACCOUNTING_MODE':'answer-v1'},runtime)).client()
            client.supervisor.command=command
            from vmodel_contracts.interactive import StartSessionRequest,InputResponse
            start=client.start(StartSessionRequest(session_id=sid,task_id=tid,idea='A test app',model_route='deepseek',initial_model='deepseek-flash'))
            assert start.snapshot.open_input is not None
            legacy_before=list(legacy);call_count=len(calls)
            from delivery_api.child_broker_journal import ChildBrokerJournal
            def no_journal(*a,**kw):raise AssertionError('two-touch answer must not create a durable per-call journal')
            monkeypatch.setattr(ChildBrokerJournal,'__init__',no_journal)
            original=AnswerReceiptStore._call
            def counted(self,sql,params):payments.append(sql);return original(self,sql,params)
            monkeypatch.setattr(AnswerReceiptStore,'_call',counted)
            from delivery_api import postgres_scope
            stats=postgres_scope.RequestStats();token=postgres_scope.request_stats.set(stats)
            answer_phase[0]=True
            response=InputResponse(input_id=start.snapshot.open_input.input_id,
                value='Bookings contain a customer and a time slot',actor_id='fake',expected_sequence=start.snapshot.sequence)
            def submit():
                with tenant_context(w.scope):return client.submit(sid,response)
            if outcome=='stop':
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(1) as pool:
                    future=pool.submit(submit);assert entered.wait(10)
                    w.store.request_task_stop(tid);client.stop(sid);release.set()
                    with pytest.raises(Exception):future.result(timeout=20)
            elif outcome=='http-error':
                with pytest.raises(Exception):submit()
            else:
                result=submit();assert result.snapshot.sequence>start.snapshot.sequence
            postgres_scope.request_stats.reset(token)
            print('PARENT_ANSWER_TRACE',outcome,sum(e[3] for e in stats.trace),stats.transactions)
            assert len(calls)>call_count
            trace=getattr(client,'_last_answer_trace',{'total_syncs':None,'child':None})
            if os.getenv('ANSWER_TRACE_DUMP'):print('ANSWER_TRACE_DUMP',json.dumps(trace))
            print('ANSWER_TOTAL',outcome,trace['total_syncs'],sum(v['syncs'] for v in trace['child']['callers'].values()) if trace['child'] else None)
            from delivery_api.answer_trace import exported_lines
            assert 'answer.trace' in exported_lines() and sid in exported_lines()
            assert '"field":"estimate_units"' in exported_lines()
            assert '"category":"callers"' in exported_lines()
            for line in exported_lines().splitlines():json.loads(line[len('answer.trace '):])
            if outcome=='success':
                # Exercise the exact req-debug collector with REAL retained
                # answer rows. Only the unavailable Docker transport is doubled;
                # this is not a claim of target-container/Docker verification.
                import contextlib,io,runpy,subprocess,zipfile
                from types import SimpleNamespace
                retained=exported_lines();run=subprocess.run
                def local_transport(command,**kwargs):
                    if command[:2]==['docker','ps']:
                        return SimpleNamespace(stdout='requi-preview-exec-local-proof\n',returncode=0)
                    if command[:2]==['docker','exec']:
                        if 'answer_diagnostics' in command[5]:
                            from delivery_api.answer_diagnostics import exported_json
                            return SimpleNamespace(stdout=exported_json(),returncode=0)
                        return SimpleNamespace(stdout=retained,returncode=0)
                    if command[0]=='docker':
                        return SimpleNamespace(stdout='local test transport, not real Docker\n',returncode=0)
                    return run(command,**kwargs)
                output=io.StringIO()
                with monkeypatch.context() as patch:
                    patch.setattr(subprocess,'run',local_transport)
                    patch.setattr(sys,'argv',['preview-debug.py','--code','LOCAL-ANSWER-PROOF'])
                    patch.setattr(Path,'home',classmethod(lambda cls:tmp_path))
                    with contextlib.redirect_stdout(output):runpy.run_path('scripts/ops/preview-debug.py',run_name='__main__')
                bundle=Path(output.getvalue().split('Debug ZIP: ')[1].splitlines()[0])
                try:
                    with zipfile.ZipFile(bundle) as z:
                        lines=z.read('answer-traces.jsonl').decode()
                        own=[json.loads(line[len('answer.trace '):]) for line in lines.splitlines() if line.startswith('answer.trace ') and sid in line]
                        assert any(row.get('field')=='total_syncs' and row['value']==trace['total_syncs'] for row in own)
                        assert any(row.get('source')=='parent_wire' and row.get('complete') is True for row in own)
                        assert any(row.get('source')=='child' and row.get('complete') is True for row in own)
                        assert any(row.get('category')=='accounting' for row in own)
                        assert any(row.get('source')=='engine_timing' for row in own)
                        assert 'fake-parent-only' not in lines and 'REQUI_ANSWER_RESULT' not in lines
                finally:bundle.unlink()
                page=client.progress(sid,after=10)
                assert page.events and page.next_cursor>10
                assert all(e.session_id==sid and e.task_id==tid for e in page.events)
                assert trace['engine_timings']
                assert not any('add_engine_timing_batch' in row['caller'] and row['syncs'] for row in trace['parent_wire'])
                assert not any('append_observational_progress' in name and row['syncs'] for name,row in trace['child']['callers'].items())
                assert trace['child']['complete']
                assert sum(v['syncs'] for v in trace['child']['callers'].values())>0
                assert trace['total_syncs']>trace['parent']['syncs']
                next_input=InputResponse(input_id=result.snapshot.open_input.input_id,value='not dispatched',actor_id='fake',expected_sequence=result.snapshot.sequence)
                for stale in (next_input.model_copy(update={'input_id':uuid.uuid4()}),next_input.model_copy(update={'expected_sequence':result.snapshot.sequence+1})):
                    with pytest.raises(PermissionError,match='input or sequence changed'):client.submit(sid,stale)
                assert len(payments)==2,'stale input must fail before another debit/provider request'
            assert legacy==legacy_before
            assert 'requi_answer_start' in payments[0]
            if outcome=='success':assert len(payments)==2 and 'requi_answer_finish' in payments[1]
            else:assert len(payments) in (1,2)
            with w.admin() as c:
                receipt=c.execute('SELECT state,estimate_units,actual_units,job_id FROM answer_charge_receipts WHERE owner_id=%s',(w.scope.owner_id,)).fetchone()
                balance=c.execute('SELECT balance_units FROM credit_accounts WHERE owner_id=%s',(w.scope.owner_id,)).fetchone()[0]
            assert receipt[1]==100000000
            if outcome in ('success','stop'):
                assert receipt[0]=='settled' and balance==1000000000-receipt[2]
                totals=w.store.retail_phase_totals(receipt[3]);assert sum(v['posted_units'] for v in totals.values())==receipt[2]
                assert w.store.worker_job(receipt[3])['state']=='done'
                before=len(calls)
                if outcome=='success':
                    assert client.submit(sid,response).snapshot.sequence==result.snapshot.sequence
                    with pytest.raises(ValueError,match='conflicting'):client.submit(sid,response.model_copy(update={'value':'changed'}))
                    assert client.resume(receipt[3]).sequence==result.snapshot.sequence
                else:
                    with pytest.raises(PermissionError,match='stopped'):client.resume(receipt[3])
                assert len(calls)==before
                if outcome=='success':
                    # Resume above deliberately recycled the original child.
                    # Prove a cold next answer and then a genuine warm follow-up.
                    current=result.snapshot
                    parked_pid=None
                    for turn in range(2):
                        next_response=InputResponse(input_id=current.open_input.input_id,value='Customers choose the slot and confirm',actor_id='fake',expected_sequence=current.sequence)
                        current=client.submit(sid,next_response).snapshot
                        assert current.sequence>next_response.expected_sequence
                        live_pid=client._warm[sid]['handle'].process.pid
                        if parked_pid is not None:assert live_pid==parked_pid
                        parked_pid=live_pid
                        trace2=client._last_answer_trace
                        if os.getenv('ANSWER_TRACE_DUMP'):print('ANSWER_TRACE_DUMP',json.dumps(trace2))
                        print('ANSWER_FOLLOWUP',turn,trace2['total_syncs'],trace2['parent']['syncs'],sum(v['syncs'] for v in trace2['child']['callers'].values()))
                        assert trace2['child']['complete'] and trace2['parent_wire_complete']
                    with w.admin() as c:
                        receipts=c.execute('SELECT state,actual_units FROM answer_charge_receipts WHERE owner_id=%s',(w.scope.owner_id,)).fetchall()
                        balance=c.execute('SELECT balance_units FROM credit_accounts WHERE owner_id=%s',(w.scope.owner_id,)).fetchone()[0]
                    assert len(receipts)==3 and all(row[0]=='settled' for row in receipts)
                    assert balance==1000000000-sum(row[1] for row in receipts)
                    assert len(payments)==6
                if outcome=='stop':
                    assert len(calls)-call_count==1
                    assert client.snapshot(sid).open_input is None
            else:
                assert receipt[0]=='pending' and receipt[2] is None and balance==900000000
                assert w.store.retail_phase_totals(receipt[3])=={}
                before=len(calls)
                with pytest.raises(RuntimeError,match='pending'):client.submit(sid,response)
                assert len(calls)==before
    finally:
        release.set()
        if client is not None:client.close()
        server.shutdown();server.server_close()
