"""Disposable PG + fake provider, no public route or process spawning."""
import asyncio,os,threading
from types import SimpleNamespace
import pytest
from test_attempt_ledger import ledger,DSN,Q
from test_postgres_store import store,A,B
from delivery_api.tenancy import tenant_context,TenantAccessDenied,session_root
from delivery_api.producer_admission import WorkerAdmissionGateway
from delivery_api.same_host_producer import SameHostProducerWorker
pytestmark=pytest.mark.skipif(not DSN,reason='disposable Postgres required')

@pytest.fixture
def worker(ledger,tmp_path,monkeypatch):
    source=os.getenv('DELIVERY_TEST_SPEC_SOURCE')
    if not source:pytest.skip('explicit hermetic producer source required')
    monkeypatch.syspath_prepend(source)
    monkeypatch.delenv('VMODEL_TENANT_OWNER',raising=False)
    monkeypatch.delenv('INTERVIEW_ENGINE_TENANTS',raising=False)
    s,tid,job=ledger
    with tenant_context(A):s.pause_interrupted_worker(job)
    gateway=lambda cap,jid:WorkerAdmissionGateway(s,jid,require_prepaid=False,
        resolve_quote=lambda **kw:(Q,'fake complete request'),
        measure_usage=lambda response:response.usage_metadata)
    class Log:
        def record(self,*args):raise AssertionError('cloud usage must use ledger')
    return SameHostProducerWorker(s,tmp_path,gateway_factory=gateway,usage_log_factory=lambda *args:Log()),s,tid,job,tmp_path

OPTIONS=dict(stage='needs',provider='fake',model='fake',messages=['fake'],max_output_tokens=10)


def test_reusable_worker_claims_once_and_restores_context(worker):
    w,s,tid,job,base=worker;calls=[]
    from spec_generator.attempt_admission import admitted_invoke,_cloud
    def producer(cap,root):
        assert root==session_root(base,A,cap.session_id)
        calls.append(cap)
        admitted_invoke(lambda:SimpleNamespace(usage_metadata={'input_tokens':2,'output_tokens':3}),**OPTIONS)
    with tenant_context(B):assert not asyncio.run(w.execute(job,producer))
    with tenant_context(A):
        assert asyncio.run(w.execute(job,producer))
        assert not asyncio.run(w.execute(job,producer))
        assert s.worker_job(job)['state']=='done'
        with s._postgres.transaction() as c:
            row=c.execute('SELECT state,expense_actual,retail_actual FROM provider_attempts WHERE job_id=%s',(job,)).fetchone()
        assert row=={'state':'settled','expense_actual':8,'retail_actual':18}
    assert len(calls)==1 and not _cloud()


def test_same_marker_stop_bills_accepted_discards_late_result_blocks_next(worker):
    w,s,tid,job,base=worker;calls=[];writes=[]
    from spec_generator.attempt_admission import admitted_invoke
    from spec_generator.stop_fence import StopFence,StopRequested,guarded_publish
    def producer(cap,root):
        def accepted():
            calls.append(1);s.request_task_stop(tid)
            StopFence(root/'interview'/'stop-request.json').request_stop()
            return SimpleNamespace(usage_metadata={'input_tokens':2,'output_tokens':3})
        with pytest.raises(StopRequested):admitted_invoke(accepted,**OPTIONS)
        with pytest.raises(StopRequested):guarded_publish(lambda:writes.append(1))
        admitted_invoke(accepted,**OPTIONS)
    with tenant_context(A):
        assert asyncio.run(w.execute(job,producer))
        assert s.worker_job(job)['state']=='done' and s.task(tid)['state']=='stop_requested'
        with s._postgres.transaction() as c:
            rows=c.execute('SELECT state,expense_actual FROM provider_attempts WHERE job_id=%s',(job,)).fetchall()
        assert rows==[{'state':'settled','expense_actual':8}]
        s.confirm_task_stopped(tid,engine_quiescent=True)
    assert calls==[1] and writes==[]


def test_deadline_thread_keeps_job_open_until_explicit_owner_recovery(worker):
    w,s,tid,job,base=worker;finish=threading.Event();exited=threading.Event()
    from spec_generator.model_config import _run_with_deadline
    from spec_generator.stop_fence import StopFence
    def producer(cap,root):
        def accepted():
            try:finish.wait(3)
            finally:exited.set()
        with pytest.raises(TimeoutError):_run_with_deadline(accepted,.02,'fake')
        s.request_task_stop(tid);StopFence(root/'interview'/'stop-request.json').request_stop()
    with tenant_context(A):
        assert asyncio.run(w.execute(job,producer))
        assert s.worker_job(job)['state']=='running'
        restored=SameHostProducerWorker(s,base,gateway_factory=None,usage_log_factory=None)
        assert not restored.reconcile_stopped(job)
    with tenant_context(B):
        with pytest.raises(KeyError):restored.reconcile_stopped(job)
    finish.set();assert exited.wait(2)
    with tenant_context(A):
        for _ in range(100):
            if restored.reconcile_stopped(job):break
            threading.Event().wait(.005)
        assert s.worker_job(job)['state']=='done'
        s.confirm_task_stopped(tid,engine_quiescent=True)


def test_unsafe_session_and_gateway_mismatch_never_run(worker):
    w,s,tid,job,base=worker;calls=[]
    with tenant_context(A):
        w.gateway_factory=lambda cap,jid:SimpleNamespace(store=s,job_id='other')
        with pytest.raises(TenantAccessDenied):asyncio.run(w.execute(job,lambda *args:calls.append(1)))
    assert calls==[]


def test_normal_return_with_surviving_writer_keeps_job_running(worker):
    w,s,tid,job,base=worker;finish=threading.Event();exited=threading.Event()
    from spec_generator.model_config import _run_with_deadline
    from spec_generator.stop_fence import StopFence
    def producer(cap,root):
        def accepted():
            try:finish.wait(3)
            finally:exited.set()
        with pytest.raises(TimeoutError):_run_with_deadline(accepted,.02,'fake')
    with tenant_context(A):
        assert asyncio.run(w.execute(job,producer))
        assert s.worker_job(job)['state']=='running'
        # Owner explicitly requests Stop, no automatic restart or retry.
        s.request_task_stop(tid)
        StopFence(session_root(base,A,s.engine_session(tid))/'interview'/'stop-request.json').request_stop()
        assert not w.reconcile_stopped(job)
    finish.set();assert exited.wait(2)
    with tenant_context(A):
        for _ in range(100):
            if w.reconcile_stopped(job):break
            threading.Event().wait(.005)
        assert s.worker_job(job)['state']=='done'
        s.confirm_task_stopped(tid,engine_quiescent=True)


def test_timeout_failure_does_not_close_a_surviving_writer(worker):
    w,s,tid,job,base=worker;finish=threading.Event();exited=threading.Event()
    from spec_generator.model_config import _run_with_deadline
    from spec_generator.stop_fence import StopFence
    def producer(cap,root):
        def accepted():
            try:finish.wait(3)
            finally:exited.set()
        _run_with_deadline(accepted,.02,'fake')
    with tenant_context(A):
        with pytest.raises(TimeoutError):asyncio.run(w.execute(job,producer))
        assert s.worker_job(job)['state']=='running'
        s.request_task_stop(tid)
        StopFence(session_root(base,A,s.engine_session(tid))/'interview'/'stop-request.json').request_stop()
        assert not w.reconcile_stopped(job)
    finish.set();assert exited.wait(2)
    with tenant_context(A):
        for _ in range(100):
            if w.reconcile_stopped(job):break
            threading.Event().wait(.005)
        assert s.worker_job(job)['state']=='done'
        s.confirm_task_stopped(tid,engine_quiescent=True)


def test_missing_accepted_usage_keeps_provider_hold_after_writer_closes(worker):
    w,s,tid,job,base=worker
    from spec_generator.attempt_admission import admitted_invoke
    from spec_generator.stop_fence import StopFence
    from delivery_api.attempt_ledger import AdmissionDenied
    def producer(cap,root):
        def accepted():
            s.request_task_stop(tid);StopFence(root/'interview'/'stop-request.json').request_stop()
            return SimpleNamespace(usage_metadata=None)
        admitted_invoke(accepted,**OPTIONS)
    with tenant_context(A):
        with pytest.raises(AdmissionDenied):asyncio.run(w.execute(job,producer))
        assert s.worker_job(job)['state']=='done'
        with s._postgres.transaction() as c:
            row=c.execute('SELECT state,expense_actual FROM provider_attempts WHERE job_id=%s',(job,)).fetchone()
        assert row=={'state':'uncertain','expense_actual':None}
        with pytest.raises(AdmissionDenied):s.confirm_task_stopped(tid,engine_quiescent=True)
        assert s.task(tid)['state']=='stop_requested'
        # Disposable operator cleanup only, not a production reconciliation.
        import psycopg
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM provider_attempts WHERE job_id=%s',(job,))
        s.confirm_task_stopped(tid,engine_quiescent=True)


def test_symlinked_marker_rejected_before_producer_contact(worker,tmp_path):
    w,s,tid,job,base=worker;calls=[]
    with tenant_context(A):
        root=session_root(base,A,s.engine_session(tid));(root/'interview').mkdir(parents=True)
        outside=tmp_path/'outside.json';outside.write_text('outside must remain')
        (root/'interview'/'stop-request.json').symlink_to(outside)
        with pytest.raises(TenantAccessDenied):asyncio.run(w.execute(job,lambda *args:calls.append(1)))
    assert calls==[] and outside.read_text()=='outside must remain'


@pytest.mark.parametrize('stream',[False,True])
@pytest.mark.parametrize('stopped',[False,True])
@pytest.mark.parametrize('prepaid',[False,True])
def test_private_worker_actual_sdk_factory_raw_wire_once(worker,monkeypatch,stream,stopped,prepaid):
    import httpx,json,base64
    w,s,tid,job,base=worker;clients=[];captured=[]
    from spec_generator import model_config as module
    from spec_generator.provider_transport import AdmittedTransport,AsyncAdmittedTransport
    monkeypatch.setattr(module,'for_stage',lambda stage:SimpleNamespace(provider='deepseek',model='fake',max_tokens=10,temperature=0,seed=None))
    monkeypatch.setattr(module,'_check_truncated',lambda *args:None)
    monkeypatch.setenv('DEEPSEEK_API_KEY','fake-not-used')
    # Fake reviewed serialized quote; deliberately omit messages resolver so any
    # double admission through the old wrapper fails before fake contact.
    import hashlib
    w.gateway_factory=lambda cap,jid:WorkerAdmissionGateway(s,jid,require_prepaid=False,
        resolve_serialized=lambda **kw:({**Q,'provider':'deepseek'},hashlib.sha256(kw['serialized_request']).hexdigest()),
        measure_raw_usage=lambda usage:{'input_tokens':usage['prompt_tokens'],'output_tokens':usage['completion_tokens']})
    if prepaid:
        import psycopg
        with psycopg.connect(DSN) as c:
            c.execute('INSERT INTO credit_accounts VALUES (%s,1,1000000,0)',(A.owner_id,))
            c.execute('UPDATE admission_limits SET expense_limit=NULL,retail_limit=0,attempt_expense_max=NULL,attempt_retail_max=1,rate_max=NULL,concurrency_max=3 WHERE owner_id=%s',(A.owner_id,))
        w.gateway_factory=lambda cap,jid:WorkerAdmissionGateway(s,jid,
            resolve_serialized=lambda **kw:({**Q,'provider':'deepseek','retail_units_per_credit':1000000},hashlib.sha256(kw['serialized_request']).hexdigest()),
            measure_raw_usage=lambda usage:{'input_tokens':usage['prompt_tokens'],'output_tokens':usage['completion_tokens']})
    usage={'prompt_tokens':2,'completion_tokens':3,'total_tokens':5}
    def fake(request):
        captured.append(request.content)
        with s._postgres.transaction() as c:
            rows=c.execute('SELECT state FROM provider_attempts WHERE job_id=%s',(job,)).fetchall()
        assert rows==[{'state':'dispatched'}]
        if stopped:
            from spec_generator.stop_fence import StopFence
            s.request_task_stop(tid)
            StopFence(session_root(base,A,s.engine_session(tid))/'interview'/'stop-request.json').request_stop()
        if not json.loads(request.content).get('stream'):
            return httpx.Response(200,json={'id':'fake','object':'chat.completion','model':'fake','choices':[{'index':0,'message':{'role':'assistant','content':'ok'},'finish_reason':'stop'}],'usage':usage})
        common={'id':'fake','object':'chat.completion.chunk','model':'fake'}
        events=[common|{'choices':[{'index':0,'delta':{'role':'assistant','content':'ok'},'finish_reason':None}]},common|{'choices':[],'usage':usage}]
        raw=b''.join(('data: '+json.dumps(e)+'\n\n').encode() for e in events)+b'data: [DONE]\n\n'
        class Chunks(httpx.SyncByteStream):
            def __iter__(self):
                for i in range(0,len(raw),9):yield raw[i:i+9]
        return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Chunks())
    def client_factory(**kw):
        gateway=kw.pop('gateway')
        pair=(httpx.Client(transport=AdmittedTransport(httpx.MockTransport(fake),gateway,**kw)),httpx.AsyncClient(transport=AsyncAdmittedTransport(httpx.MockTransport(fake),gateway,**kw)))
        clients.extend(pair);return pair
    w.wire_client_factory=client_factory
    def producer(cap,root):
        model=module.build_chat_model('needs',structured=False)
        def call():
            if stream:return ''.join(c.content for c in module.stream_checked(model,['fake'],'needs'))
            return module.invoke_checked(model,['fake'],'needs').content
        if stopped:
            from spec_generator.stop_fence import StopRequested
            with pytest.raises(StopRequested):call()
            call()  # expected Stop escapes to the worker control-state adapter
        else:assert call()=='ok'
    try:
        with tenant_context(A):
            assert asyncio.run(w.execute(job,producer))
            assert s.worker_job(job)['state']=='done'
            with s._postgres.transaction() as c:
                rows=c.execute('SELECT state,expense_actual,retail_actual,raw_usage FROM provider_attempts WHERE job_id=%s',(job,)).fetchall()
            assert rows==[{'state':'settled','expense_actual':8,'retail_actual':18,'raw_usage':usage}]
            if prepaid:
                with s._postgres.transaction() as c:assert c.execute('SELECT balance_units FROM credit_accounts WHERE owner_id=%s',(A.owner_id,)).fetchone()['balance_units']==-17
            if stopped:
                assert s.task(tid)['state']=='stop_requested'
                s.confirm_task_stopped(tid,engine_quiescent=True)
        assert len(captured)==1
    finally:
        for c in clients:
            if isinstance(c,httpx.Client):c.close()
            else:asyncio.run(c.aclose())
        if prepaid:
            with psycopg.connect(DSN) as c:
                c.execute('DELETE FROM credit_charges WHERE owner_id=%s',(A.owner_id,))
                c.execute('DELETE FROM credit_accounts WHERE owner_id=%s',(A.owner_id,))


@pytest.mark.parametrize('stopped',[False,True])
@pytest.mark.parametrize('bridge',[False,True])
def test_private_worker_actual_fresh_needs_graph_checkpoint_is_owner_bound(worker,monkeypatch,stopped,bridge):
    from spec_generator.worker_graph import run_fresh_graph
    from spec_generator.worker_runtime import JobRuntime
    from spec_generator.model_config import StageModel
    from spec_generator.telemetry import UsageLog
    from spec_generator.progress import ProgressBus
    from vmodel_contracts import CONTRACT_VERSION
    from spec_generator.stop_fence import StopFence
    import spec_generator.skeleton as skeleton
    w,s,tid,job,base=worker;events=[];outputs=[];lines=[]
    w.runtime_factory=lambda cap,root:JobRuntime({'needs':StageModel('fake','fake')},str(root/'calls.jsonl'),lines.append)
    if stopped:
        original=skeleton.needs_from_intent
        def stop_before_publication(value):
            result=original(value);s.request_task_stop(tid)
            StopFence(session_root(base,A,s.engine_session(tid))/'interview'/'stop-request.json').request_stop()
            return result
        monkeypatch.setattr(skeleton,'needs_from_intent',stop_before_publication)
    def inputs(cap,root):
        intent={'contract_version':CONTRACT_VERSION,'session_id':cap.session_id,'problem_statement':'Record task evidence','target_users':['People'],'core_value_proposition':'Task evidence',
            'stakeholder_needs':[{'need_id':'SN-1','statement':'People need recorded task evidence','kind':'functional','source_turns':[0]}],
            'in_scope':['Record task evidence'],'out_of_scope':[],'external_integrations':[],'constraints':[],'open_questions':[],'source_turns':{}}
        conops={'contract_version':CONTRACT_VERSION,'session_id':cap.session_id,
            'stakeholder_context':[{'stakeholder':'People','role':'User','goals':['Record evidence'],'operating_environment':'Office','source_turns':[0]}],
            'operational_scenarios':[{'scenario_id':'OS-1','name':'Record evidence','trigger':'Task ends','actors':['People'],'operational_flow':['Record evidence'],'success_outcome':'Evidence recorded','source_turns':[0]}],
            'concept_options':[{'name':'Form','summary':'Record through a form','advantages':['Simple'],'disadvantages':['Manual']},{'name':'App','summary':'Record through an app','advantages':['Mobile'],'disadvantages':['Install']}],
            'decision':{'selected_concept':'Form','rationale':'Simple','founder_confirmed':True,'source_turns':[0]}}
        baseline={'contract_version':CONTRACT_VERSION,'baseline_id':'fake-reviewed-baseline','system_name':'Test','mode':'greenfield','repositories':[],'immutable_decisions':[]}
        return {'intent':intent,'conops':conops,'baseline':baseline}
    def producer(cap,root):
        bus=ProgressBus();bus.subscribe(events.append)
        outputs.append(run_fresh_graph(capability=cap,session_root=root,**inputs(cap,root),log=UsageLog(cap.session_id),progress=bus,stop_after='needs'))
    async def dispatch():
        if not bridge:return await w.execute(job,producer)
        from delivery_api.fresh_graph_job import execute_fresh_needs
        bus=ProgressBus();bus.subscribe(events.append)
        result=await execute_fresh_needs(s,w,job,input_factory=inputs,log_factory=lambda cap,root:UsageLog(cap.session_id),progress_factory=lambda *args:bus)
        outputs.extend(result['results']);return result['started']
    with tenant_context(B):
        if bridge:
            with pytest.raises(KeyError):asyncio.run(dispatch())
        else:assert not asyncio.run(dispatch())
    with tenant_context(A):
        assert asyncio.run(dispatch())
        assert s.worker_job(job)['state']=='done'
        root=session_root(base,A,s.engine_session(tid))
        if stopped:
            assert outputs==[] and not (root/'spec'/'needs.json').exists()
            s.confirm_task_stopped(tid,engine_quiescent=True)
        else:
            assert len(outputs)==1 and (root/'spec'/'needs.json').is_file()
            with s._postgres.transaction() as c:
                assert c.execute('SELECT count(*) n FROM provider_attempts WHERE job_id=%s',(job,)).fetchone()['n']==0
        assert not (base/'tenants'/B.owner_id/'sessions'/s.engine_session(tid)).exists()


def test_cancelled_dispatch_does_not_cancel_accepted_thread_or_close_job(worker):
    w,s,tid,job,base=worker;entered=threading.Event();finish=threading.Event();exited=threading.Event()
    from spec_generator.stop_fence import StopFence
    def producer(cap,root):
        entered.set()
        try:finish.wait(3)
        finally:exited.set()
    async def interrupted():
        dispatch=asyncio.create_task(w.execute(job,producer))
        assert await asyncio.to_thread(entered.wait,2)
        dispatch.cancel()
        with pytest.raises(asyncio.CancelledError):await dispatch
        assert s.worker_job(job)['state']=='running' and not exited.is_set()
        s.request_task_stop(tid)
        StopFence(session_root(base,A,s.engine_session(tid))/'interview'/'stop-request.json').request_stop()
        assert not w.reconcile_stopped(job)
        finish.set();assert await asyncio.to_thread(exited.wait,2)
        for _ in range(100):
            if w.reconcile_stopped(job):break
            await asyncio.sleep(.005)
        assert s.worker_job(job)['state']=='done'
        s.confirm_task_stopped(tid,engine_quiescent=True)
    with tenant_context(A):asyncio.run(interrupted())


@pytest.mark.parametrize('stale',[False,True])
def test_durable_srs_rework_job_retains_proposal_only_for_same_gate(worker,monkeypatch,stale):
    from delivery_api.srs_rework_job import queue_proposal,execute_proposal
    from spec_generator.srs_rework import prepare_rework
    from vmodel_contracts.semantic_review import make_review
    from vmodel_contracts import SRSDocument,CONTRACT_VERSION
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.checkpoint.base import empty_checkpoint
    from spec_generator.graph import checkpoint_serde
    import sqlite3
    w,s,tid,old_job,base=worker;calls=[]
    with tenant_context(A):
        s.claim_worker(old_job);s.finish_worker(old_job)
        s.wait_at_gate(tid,'srs')
        sid=s.engine_session(tid);root=session_root(base,A,sid);(root/'spec').mkdir(parents=True)
        intent={'contract_version':CONTRACT_VERSION,'session_id':sid,'problem_statement':'Record task evidence','constraints':[],'assumptions':[],'glossary':{},'target_users':['People'],'core_value_proposition':'Task evidence','stakeholder_needs':[{'need_id':'SN-1','statement':'People need recorded evidence','kind':'functional','source_turns':[0]}],'in_scope':['Record evidence'],'out_of_scope':[],'source_turns':{}}
        req={'requirement_id':'R-1','title':'Task evidence','statement':'The system shall use SQLite to record task evidence.','kind':'functional','priority':'must','need_ids':['N-1'],'intent_fields':['problem_statement'],'acceptance':[{'criterion_id':'AC-1','statement':'Task evidence is recorded.'}]}
        candidate=SRSDocument.model_validate({'session_id':sid,'intent_model_ref':sid,'purpose':'Test','scope_summary':'Evidence','functional':[req],'non_functional':[]}).model_dump(mode='json')
        review=make_review(sid,'srs',candidate,[{'finding_id':'f','item_id':'R-1','path':['functional',0],'rule':'solution_language','reason':'Implementation choice','original':candidate['functional'][0]}]).model_dump(mode='json')
        path=root/'spec'/'checkpoints.sqlite'
        with sqlite3.connect(path,check_same_thread=False) as conn:
            cp=empty_checkpoint();cp['channel_values']={'intent_model':intent,'needs':{'needs':[]},'conops':None,'srs':candidate,'semantic_reviews':{'srs':review}}
            cfg=SqliteSaver(conn,serde=checkpoint_serde()).put({'configurable':{'thread_id':sid,'checkpoint_ns':''}},cp,{'source':'input','step':0,'parents':{}},{})
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        before=path.read_bytes()
        job=queue_proposal(s,tid,source_checkpoint_id=cfg['configurable']['checkpoint_id'],source_review_revision=review['revision'],item_ids=['R-1'],guidance='Behavior only')
        assert s.worker_job(job)['execution_options']['item_ids']==['R-1']
    with tenant_context(B):
        with pytest.raises(KeyError):asyncio.run(execute_proposal(s,w,job,log_factory=lambda *args:None))
    def prepare(**kw):
        calls.append(kw['item_ids']);kw.pop('log');kw.pop('progress')
        result=prepare_rework(**kw,produce=lambda **ctx:{**ctx['original'],'statement':'The system shall record task evidence.'})
        if stale:
            gate=next(g for g in s.task(tid)['gates'] if g['after_phase']=='srs')
            s.decide_gate(tid,gate['gate_key'],'approve',selected_option=gate['options'][0],actor='test',expected_revision=gate['revision'])
        return result
    with tenant_context(A):
        if stale:
            with pytest.raises(ValueError,match='gate changed'):asyncio.run(execute_proposal(s,w,job,log_factory=lambda *args:None,prepare=prepare))
            assert not (root/'spec'/'rework-proposals').exists()
        else:
            result=asyncio.run(execute_proposal(s,w,job,log_factory=lambda *args:None,prepare=prepare))
            assert len(result['proposals'])==1 and not result['source_committed']
            assert result['proposals'][0]['result']['review']['decisions']==[]
            assert s.worker_job(job)['state']=='done'
            assert not asyncio.run(w.execute(job,lambda *args:None))
        assert path.read_bytes()==before and not (root/'spec'/'srs.json').exists()
        s.finish_worker(job)
        s.request_task_stop(tid);s.confirm_task_stopped(tid,engine_quiescent=True)
        # Disposable fixture cleanup of a failed proposal row, not live recovery.
        import psycopg
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM worker_jobs WHERE job_id=%s',(job,))
    assert calls==[['R-1']]


def test_queued_srs_proposal_gate_revision_must_not_be_rebased_at_dispatch(worker):
    from delivery_api.srs_rework_job import queue_proposal,execute_proposal
    w,s,tid,old_job,base=worker;calls=[]
    with tenant_context(A):
        s.claim_worker(old_job);s.finish_worker(old_job);s.wait_at_gate(tid,'srs')
        job=queue_proposal(s,tid,source_checkpoint_id='fake-checkpoint',source_review_revision='a'*64,item_ids=['R-1'],guidance='Behavior only')
        queued=s.worker_job(job)['execution_options']['gate_revision']
        gate=next(g for g in s.task(tid)['gates'] if g['after_phase']=='srs')
        s.decide_gate(tid,gate['gate_key'],'approve',selected_option='fake',actor='test',expected_revision=queued)
        s.wait_at_gate(tid,'srs')
        with pytest.raises(ValueError,match='gate changed before'):
            asyncio.run(execute_proposal(s,w,job,log_factory=lambda *args:calls.append(1)))
        assert calls==[] and s.worker_job(job)['state']=='failed'
        s.request_task_stop(tid);s.confirm_task_stopped(tid,engine_quiescent=True)
        import psycopg
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM worker_jobs WHERE job_id=%s',(job,))


def test_fresh_phase_bridge_rejects_runtime_model_drift_before_inputs(worker):
    from delivery_api.fresh_graph_job import execute_fresh_needs
    from spec_generator.worker_runtime import JobRuntime
    from spec_generator.model_config import StageModel
    w,s,tid,old_job,base=worker;calls=[]
    w.runtime_factory=lambda cap,root:JobRuntime({'needs':StageModel('deepseek','different')},str(root/'calls.jsonl'),lambda text:None)
    with tenant_context(A):
        s.claim_worker(old_job);s.finish_worker(old_job)
        job=s.enqueue_worker(tid,'phase:needs',execution_options={'mode':'start','model_route':'deepseek:deepseek-chat'})
        with pytest.raises(TenantAccessDenied,match='queued model route'):
            asyncio.run(execute_fresh_needs(s,w,job,input_factory=lambda *args:calls.append(1),log_factory=lambda *args:None))
        assert calls==[] and s.worker_job(job)['state']=='failed'
        assert not (session_root(base,A,s.engine_session(tid))/'spec').exists()
        s.request_task_stop(tid);s.confirm_task_stopped(tid,engine_quiescent=True)
        import psycopg
        with psycopg.connect(DSN) as c:c.execute('DELETE FROM worker_jobs WHERE job_id=%s',(job,))


def test_private_worker_rejects_outside_runtime_sink_before_producer(worker):
    from spec_generator.worker_runtime import JobRuntime
    from spec_generator.model_config import StageModel
    w,s,tid,job,base=worker;calls=[]
    outside=base/'shared-calls.jsonl'
    w.runtime_factory=lambda cap,root:JobRuntime({'needs':StageModel('fake','fake')},str(outside),lambda text:None)
    with tenant_context(A):
        with pytest.raises(ValueError,match='owned runtime log'):
            asyncio.run(w.execute(job,lambda *args:calls.append(1)))
        assert calls==[] and not outside.exists() and s.worker_job(job)['state']=='failed'
