"""Restricted PG worker + actual saved engine graph + new reviewed input."""
import sys,json,uuid,asyncio
from pathlib import Path
from types import SimpleNamespace
import pytest
pytest.importorskip('interview_engine.application')
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
from test_attempt_ledger import ledger
from test_postgres_store import DSN,A,B,store
from test_item6_interview import configure_interview_models
from pipeline_executor import PrivatePipeline
from delivery_api.tenancy import tenant_context,session_root
from delivery_api.producer_admission import WorkerAdmissionGateway
from langchain_core.messages import HumanMessage
from vmodel_contracts.semantic_review import make_review
from vmodel_contracts import IntentModel
from interview_engine.application import InterviewApplication
from interview_engine.stop_fence import StopFence
from vmodel_contracts.interactive import InputResponse,InputKind
pytestmark=[pytest.mark.engine_integration,pytest.mark.skipif(not DSN,reason='isolated PG required')]


@pytest.mark.parametrize('resume_pending',[False,True])
def test_exact_predecessor_new_writer_and_review_skip_old_interview(ledger,tmp_path,monkeypatch,resume_pending):
    s,tid,prior=ledger
    configure_interview_models(monkeypatch)
    from interview_engine.wire_binding import register_model
    from interview_engine import model_config as mc
    original=mc.build_chat_model
    def factory(stage,**kw):return register_model(original(stage,**kw),stage=stage,provider='deepseek',model='deepseek-chat')
    monkeypatch.setattr(mc,'build_chat_model',factory)
    monkeypatch.setenv('DEEPSEEK_API_KEY','hermetic-not-a-key')
    monkeypatch.setattr(__import__('vmodel_contracts'),'price_for',lambda *args:(0,0))
    monkeypatch.setattr(__import__('interview_engine.telemetry',fromlist=['price_for']),'price_for',lambda *args:(0,0))
    from spec_generator.worker_runtime import JobRuntime
    from spec_generator.model_config import StageModel
    from delivery_api.same_host_producer import SameHostProducerWorker
    with tenant_context(A):
        session=s.worker_job(prior)['session_id']
        root=session_root(tmp_path,A,session)
        candidate=json.loads((Path(__file__).parent/'fixtures/revision-source.json').read_text())['intent_model'];candidate['session_id']=session
        review=make_review(session,'intent',candidate,[]).model_dump(mode='json')
        resolved=IntentModel.model_validate(candidate).model_dump(mode='json')
        fence=StopFence(root/'interview/stop-request.json')
        with fence.running():
            program=InterviewApplication(session,tid,'brief',artifact_root=str(root.parent))
            program.graph.update_state(program.cfg,{'intent_model':resolved,'semantic_review':review,'semantic_review_resolved':True,'messages':[HumanMessage(content='founder source')],'submission':{'submission_id':session},'verdict':{'accepted':True,'is_web_app':True}},as_node='distill_intent')
            pending=None
            if resume_pending:
                from recovery_checkpoint import restore_for_conops
                restore_for_conops(program,review,actor=A.owner_id)
                pending=program.advance().model_dump(mode='json')
            program.checkpointer.conn.close()
        s.finish_worker(prior,failed=True)
        choices=[]
        def answer(pending):
            choices.append(pending.kind)
            if pending.kind==InputKind.CHOICE:return InputResponse(input_id=pending.input_id,actor_id=A.owner_id,value=1,rationale='fixture review')
            if pending.kind==InputKind.APPROVAL:return InputResponse(input_id=pending.input_id,actor_id=A.owner_id,value=1)
            raise ValueError('unexpected old interview question')
        runner=PrivatePipeline(store=s,artifacts_base=tmp_path,model='deepseek-chat',gateway_factory=lambda cap,job:WorkerAdmissionGateway(s,job),
            generator_clients=lambda **kw:None,interview_clients=lambda **kw:None,answer_input=answer,review_gate=lambda *a:None,progress=lambda *a:None)
        runner.generate=lambda *a:None
        # No fake provider calls are billed. Models are registered fixtures.
        runner.worker=lambda:SameHostProducerWorker(s,tmp_path,gateway_factory=runner.gateway,usage_log_factory=lambda cap,root:__import__('spec_generator.telemetry',fromlist=['UsageLog']).UsageLog(cap.session_id))
        assert runner.execute_existing(SimpleNamespace(root=tmp_path),A,s.task(tid),prior_job=prior,review=review,pending_review=pending)==tid
        assert choices==[InputKind.CHOICE,InputKind.APPROVAL]
        assert s.worker_job(prior)['state']=='failed'
        linked=s.worker_job(runner.job_ids[0])
        assert linked['state']=='done'
        assert any(e['action']=='item6.continuation.queued' and e['detail']['original_failed_job']==prior for e in s.audit_entries(tid))
        assert (root/'interview/conops.json').is_file()
