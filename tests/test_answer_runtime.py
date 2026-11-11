"""Inactive two-touch runtime seam, fake provider and real restricted PG."""
import base64,json,threading
from decimal import Decimal
import httpx,pytest
from delivery_api.answer_runtime import AnswerReceiptStore,AnswerRun,AnswerProviderBroker,AnswerComputeAccounting
from delivery_api.answer_pricing import AnswerPricing
from delivery_api.tenancy import tenant_context
from test_answer_accounting_db import answer_workspace,balance
from test_child_provider_broker import RAW,BODY,request


def test_many_provider_calls_and_compute_use_only_start_finish(answer_workspace,monkeypatch,tmp_path):
    w,job=answer_workspace;store=AnswerReceiptStore(w.store._postgres)
    trace=tmp_path/'trace';borrowed=[]
    checkout=w.store._postgres._checkout
    # Warm pool's liveness policy is tracked separately from protocol barriers.
    def traced():
        c,reused=checkout();f=trace.open('ab');c.pgconn.trace(f.fileno());borrowed.append((c,f));return c,reused
    monkeypatch.setattr(w.store._postgres,'_checkout',traced)
    with tenant_context(w.scope):
        run=AnswerRun.begin(store,answer_id='answer',job_id=job,input_id='input',input_digest='a'*64,pricing=AnswerPricing.from_environment({}))
        transport=[]
        def provider(req):transport.append(req.content);return httpx.Response(200,headers={'content-type':'application/json'},content=BODY)
        with httpx.Client(transport=httpx.MockTransport(provider),trust_env=False,follow_redirects=False) as client:
            broker=AnswerProviderBroker(run,provider='deepseek',model='deepseek-flash',client=client,endpoint='https://fixed.invalid/v1/chat/completions',headers={})
            for i in range(200):assert broker.dispatch(request()|{'call_id':'c'+str(i)})['status']==200
            assert broker.dispatch(request()|{'call_id':'c0'})['status']==200
        compute=AnswerComputeAccounting(run);compute.begin()
        compute.accept({'sequence':1,'monotonic_ns':0,'kind':'start'})
        compute.accept({'sequence':2,'monotonic_ns':1000000,'kind':'finish'});compute.finish()
        result=run.finish()
        assert result['actual_units']==12500050
        assert run.finish()==result and len(transport)==200
    for c,f in borrowed:
        c.pgconn.untrace();f.close()
    assert len(borrowed)==2
    assert sum('\tF\t' in x and x.endswith('\tSync') for x in trace.read_text().splitlines())==2
    assert balance(w)==(987499950,Decimal(0))


def test_missing_usage_does_not_block_response_but_retains_estimate(answer_workspace):
    w,job=answer_workspace
    with tenant_context(w.scope):
        run=AnswerRun.begin(AnswerReceiptStore(w.store._postgres),answer_id='answer',job_id=job,input_id='input',input_digest='a'*64,pricing=AnswerPricing.from_environment({}))
        body=b'{"choices":[],"usage":{}}'
        with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,headers={'content-type':'application/json'},content=body)),trust_env=False,follow_redirects=False) as client:
            b=AnswerProviderBroker(run,provider='deepseek',model='deepseek-flash',client=client,endpoint='https://fixed.invalid/v1/chat/completions',headers={})
            assert base64.b64decode(b.dispatch(request())['body'])==body
        run.record_compute('0')
        assert run.finish()['state']=='pending' and run.unknown_calls==1
    assert balance(w)[0]==900000000


def test_local_stop_prevents_send_and_no_db_recheck(answer_workspace):
    w,job=answer_workspace;stop=threading.Event()
    with tenant_context(w.scope):
        run=AnswerRun.begin(AnswerReceiptStore(w.store._postgres),answer_id='answer',job_id=job,input_id='input',input_digest='a'*64,pricing=AnswerPricing.from_environment({}))
        with httpx.Client(transport=httpx.MockTransport(lambda r:pytest.fail('no send')),trust_env=False,follow_redirects=False) as client:
            b=AnswerProviderBroker(run,provider='deepseek',model='deepseek-flash',client=client,endpoint='https://fixed.invalid/v1/chat/completions',headers={},stop=stop)
            stop.set()
            with pytest.raises(InterruptedError):b.dispatch(request())
    assert balance(w)[0]==900000000


def test_lost_finish_ack_replays_manifest_not_provider_calls(answer_workspace):
    w,job=answer_workspace;adapter=AnswerReceiptStore(w.store._postgres)
    with tenant_context(w.scope):
        run=AnswerRun.begin(adapter,answer_id='answer',job_id=job,input_id='input',input_digest='a'*64,pricing=AnswerPricing.from_environment({}))
        run.record_usage('attempt','intent',json.loads(BODY)['usage']);run.record_compute('0')
        original=adapter.finish;calls=[]
        def lost_ack(aid,manifest):
            result=original(aid,manifest);calls.append(result)
            if len(calls)==1:raise OSError('commit ack lost')
            return result
        adapter.finish=lost_ack
        with pytest.raises(OSError,match='ack'):run.finish()
        assert balance(w)[0]==999937500
        assert run.finish()['actual_units']==62500 and balance(w)[0]==999937500
        assert calls[0]==calls[1]


def test_rekeyed_retry_cannot_execute_replacement_job(answer_workspace):
    w,job=answer_workspace;adapter=AnswerReceiptStore(w.store._postgres)
    with tenant_context(w.scope):
        one=AnswerRun.begin(adapter,answer_id='answer',job_id=job,input_id='input',input_digest='a'*64,pricing=AnswerPricing.from_environment({}))
        tid=w.store.worker_job(job)['task_id'];other=w.store.enqueue_worker(tid,'phase:intent')
        two=AnswerRun.begin(adapter,answer_id='different',job_id=other,input_id='input',input_digest='a'*64,pricing=AnswerPricing.from_environment({}))
        assert not two.execute_allowed and two.receipt==one.receipt
        with pytest.raises(RuntimeError,match='original'):
            with two.provider_call():pytest.fail('cannot run replacement')
    assert balance(w)[0]==900000000


def test_receipt_and_usage_are_not_mutable_aliases(answer_workspace):
    w,job=answer_workspace
    with tenant_context(w.scope):
        run=AnswerRun.begin(AnswerReceiptStore(w.store._postgres),answer_id='answer',job_id=job,input_id='input',input_digest='a'*64,pricing=AnswerPricing.from_environment({}))
        r=run.receipt;r['answer_id']='foreign';assert run.receipt['answer_id']=='answer'
        run.record_compute('0')
        with run.provider_call():
            with pytest.raises(RuntimeError,match='quiescent'):run.finish()
        r=run.finish();r['actual_units']=123
        assert run.finish()['actual_units']==0


def test_tcp_transport_pool_identity_and_barriers_without_live_services(answer_workspace,tmp_path,monkeypatch):
    # Owned localhost TCP proxy to the disposable PG Unix socket. This proves
    # actual TCP framing and pooled identity isolation, NOT Supabase pooler semantics.
    import select,socket,socketserver,threading
    from contextlib import contextmanager
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    from delivery_api.postgres_scope import ScopedPostgres,RequestStats,request_stats
    w,job=answer_workspace;p=conninfo_to_dict(w.dsn)
    path=p['host']+'/.s.PGSQL.'+p.get('port','5432')
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            upstream=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            try:
                upstream.connect(path)
                while True:
                    ready,_,_=select.select([self.request,upstream],[],[],1)
                    for source in ready:
                        data=source.recv(65536)
                        if not data:return
                        (upstream if source is self.request else self.request).sendall(data)
            except (ConnectionError,OSError):pass
            finally:upstream.close()
    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address=True;daemon_threads=True
    with Server(('127.0.0.1',0),Handler) as proxy:
        thread=threading.Thread(target=proxy.serve_forever,daemon=True);thread.start()
        dsn=make_conninfo(w.dsn,user='delivery_app',host='127.0.0.1',port=proxy.server_address[1],sslmode='disable',gssencmode='disable')
        pg=None
        try:
            pg=ScopedPostgres(dsn);adapter=AnswerReceiptStore(pg);traces=[]
            original=pg._checkout
            def traced():
                c,reused=original();file=(tmp_path/('tcp-'+str(len(traces)))).open('wb');c.pgconn.trace(file.fileno());traces.append((c,file));return c,reused
            monkeypatch.setattr(pg,'_checkout',traced)
            stats=RequestStats();token=request_stats.set(stats)
            try:
                with tenant_context(w.scope):
                    run=AnswerRun.begin(adapter,answer_id='tcp',job_id=job,input_id='input',input_digest='a'*64,pricing=AnswerPricing.from_environment({}))
                    run.record_usage('tcp-attempt','intent',json.loads(BODY)['usage']);run.record_compute('1')
                    assert run.finish()['actual_units']==62550
            finally:request_stats.reset(token)
            for c,f in traces:c.pgconn.untrace();f.close()
            assert len(traces)==2
            assert sum(sum('\tF\t' in x and x.endswith('\tSync') for x in f.name and open(f.name).read().splitlines()) for c,f in traces)==2
            batches=[x for x in stats.trace if 'answer receipt [' in x[1]]
            poolchecks=[x for x in stats.trace if x[1].startswith('POOL CHECK')]
            assert len(batches)==2 and sum(x[3] for x in batches)==2
            assert sum(x[3] for x in stats.trace)==2+len(poolchecks)
            assert stats.transactions==2
            # Role/claims/autocommit do not leak through a pooled connection.
            with pg._pool.connection() as c:
                assert not c.autocommit
                assert c.execute("SELECT current_user,current_setting('request.jwt.claim.sub',true)").fetchone()['current_user']=='delivery_app'
        finally:
            if pg is not None:pg._pool.close()
            proxy.shutdown();thread.join()


def test_actual_only_display_projection_matches_settlement_not_estimate_plus_actual(answer_workspace):
    w,job=answer_workspace
    with tenant_context(w.scope):
        run=AnswerRun.begin(AnswerReceiptStore(w.store._postgres),answer_id='answer',job_id=job,input_id='input',input_digest='a'*64,pricing=AnswerPricing.from_environment({}))
        assert w.store.retail_phase_totals(job)=={}
        run.record_usage('one','intent',json.loads(BODY)['usage'])
        run.record_usage('two','probing',json.loads(BODY)['usage']);run.record_compute('0.003')
        result=run.finish();totals=w.store.retail_phase_totals(job)
        assert sum(r['credits'] for r in totals.values())==Decimal('0.00012500015')
        assert sum(r['posted_units'] for r in totals.values())==result['actual_units']
        before=totals;assert run.finish()==result and w.store.retail_phase_totals(job)==before


def test_phase_display_preserves_fractional_carry_and_never_debits_again(answer_workspace):
    w,job=answer_workspace
    with w.admin() as c:
        c.execute('UPDATE credit_accounts SET fractional_debit_units=0.95 WHERE owner_id=%s',(w.scope.owner_id,))
    with tenant_context(w.scope):
        run=AnswerRun.begin(AnswerReceiptStore(w.store._postgres),answer_id='fractions',job_id=job,input_id='input',input_digest='a'*64,pricing=AnswerPricing.from_environment({}))
        for i in range(17):
            run.record_usage('phase-'+str(i),'phase-'+str(i%7),{**json.loads(BODY)['usage'],'prompt_tokens':1,'completion_tokens':0,'total_tokens':1,'prompt_cache_miss_tokens':1})
        run.record_compute('0.003333333333333333333333333333')
        result=run.finish();totals=w.store.retail_phase_totals(job)
        assert sum(r['posted_units'] for r in totals.values())==result['actual_units']
        assert balance(w)[0]==1000000000-result['actual_units']
        assert sum(r['credits'] for r in totals.values())<Decimal('0.001')
        assert set(totals)=={'phase-'+str(i) for i in range(7)}


def test_display_insert_failure_rolls_back_correction_and_all_evidence(answer_workspace):
    w,job=answer_workspace
    with tenant_context(w.scope):
        run=AnswerRun.begin(AnswerReceiptStore(w.store._postgres),answer_id='atomic',job_id=job,input_id='input',input_digest='a'*64,pricing=AnswerPricing.from_environment({}))
        run.record_usage('atomic-attempt','intent',json.loads(BODY)['usage']);run.record_compute('0')
    # A committed legacy contribution deliberately occupies the final display key.
    # No receipt/usage/account change from finish may leak if display insertion fails.
    with w.admin() as c:
        c.execute("INSERT INTO retail_contributions SELECT 'answer-token:'||md5(jsonb_build_array(%s::uuid,%s::text,'atomic'::text,'atomic-attempt'::text)::text),%s,%s,'intent','tokens',0,0,0,%s::jsonb,0,0,1000000000,'collision',0",(w.scope.owner_id,job,w.scope.owner_id,job,json.dumps(AnswerPricing.from_environment({}).rates.snapshot())))
    with tenant_context(w.scope),pytest.raises(Exception,match='duplicate key'):
        run.finish()
    assert balance(w)[0]==900000000
    with w.admin() as c:
        assert c.execute('SELECT state,actual_units FROM answer_charge_receipts WHERE answer_id=%s',('atomic',)).fetchone()==('pending',None)
        assert c.execute('SELECT count(*) FROM answer_charge_usage WHERE answer_id=%s',('atomic',)).fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM retail_contributions WHERE job_id=%s',(job,)).fetchone()[0]==1


def test_all_answer_outcomes_log_estimate_actual_and_unknown_not_only_overruns(answer_workspace,caplog):
    import logging
    w,job=answer_workspace
    with caplog.at_level(logging.WARNING,logger='delivery_api.diagnostic'),tenant_context(w.scope):
        run=AnswerRun.begin(AnswerReceiptStore(w.store._postgres),answer_id='sample',job_id=job,input_id='private-input',input_digest='a'*64,pricing=AnswerPricing.from_environment({}))
        run.record_compute('0');run.finish();run.finish()
    lines=[r.message for r in caplog.records if r.message.startswith('answer.accounting.')]
    assert len(lines)==2
    assert 'answer.accounting.start' in lines[0] and '100000000' in lines[0]
    assert 'answer.accounting.finish' in lines[1] and 'settled' in lines[1]
    assert 'actual_units' in lines[1] and '0' in lines[1]
    assert 'private-input' not in repr(lines) and 'a'*64 not in repr(lines)
    caplog.clear()
    with caplog.at_level(logging.WARNING,logger='delivery_api.diagnostic'),tenant_context(w.scope):
        pending=AnswerRun.begin(AnswerReceiptStore(w.store._postgres),answer_id='unknown',job_id=job,input_id='next-input',input_digest='b'*64,pricing=AnswerPricing.from_environment({}))
        pending.unknown();pending.record_compute('0');pending.finish();pending.finish()
    last=[r.message for r in caplog.records if r.message.startswith('answer.accounting.finish')]
    assert len(last)==1 and '"actual_units": "None"' in last[0] and 'pending' in last[0] and 'unknown_calls' in last[0]
