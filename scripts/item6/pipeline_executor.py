"""Private same-owner graph runner. No CLI/global route override or auto answers."""
import asyncio,hashlib,json,sqlite3,uuid
from contextlib import ExitStack
from pathlib import Path
from delivery_api.tenancy import tenant_context,session_root
from delivery_api.same_host_producer import SameHostProducerWorker
from delivery_api.phases import PHASE_DEFINITIONS


class PrivatePipeline:
    def __init__(self,*,store,artifacts_base,model,gateway_factory,generator_clients,
                 interview_clients,answer_input,review_gate,progress):
        if not all(callable(x) for x in (gateway_factory,generator_clients,interview_clients,answer_input,review_gate,progress)):
            raise ValueError('explicit reviewed job clients and attended callbacks required')
        self.store=store;self.base=Path(artifacts_base);self.model=model;self.gateway=gateway_factory
        self.generator_clients=generator_clients;self.interview_clients=interview_clients
        self.answer=answer_input;self.review=review_gate;self.progress=progress;self.job_ids=[]

    def __call__(self,isolation,scope):
        # Caller has separately checked exact run go, local scope and price catalog.
        if not self.base.is_relative_to(Path(isolation.root)):
            raise ValueError('artifacts must stay inside this isolated run root')
        with tenant_context(scope):
            task=self.store.create_task('Item6 '+isolation.run_id,
                brief='A SaaS web household task board with logins for two adults to assign chores, see due dates, and confirm completion.')
            tid=task['task_id'];sid=str(uuid.uuid4());self.store.set_engine_session(tid,sid)
            self.store.set_model_default(tid,'deepseek:'+self.model)
            job=self.store.enqueue_worker(tid,'phase:intent',execution_options={'model_route':'deepseek:'+self.model})
            self.job_ids.append(job)
            worker=self.worker()
            def interview(cap,root):
                from interview_engine.application import InterviewApplication
                from interview_engine.attempt_admission import admission_gateway
                from interview_engine.wire_binding import wire_clients
                from interview_engine import stop_fence as interview_fence
                from spec_generator import stop_fence as generator_fence
                from spec_generator.cooperative_run import cli_stop_binding
                from contextlib import contextmanager
                @contextmanager
                def shared_writer():
                    fence,generation=cli_stop_binding()
                    # The same-host worker already owns the writer lease. Bind
                    # the interview's cooperative checks to that exact lease.
                    tokens=[(interview_fence._check,interview_fence._check.set(lambda:fence.check(generation))),
                        (interview_fence._current,interview_fence._current.set(fence)),
                        (interview_fence._publish,interview_fence._publish.set(generator_fence.guarded_publish))]
                    try:yield
                    finally:
                        for variable,token in reversed(tokens):variable.reset(token)
                from vmodel_contracts.interactive import InputResponse
                gateway=self.gateway(cap,job)
                with admission_gateway(gateway,required=True),wire_clients(gateway,self.interview_clients),shared_writer():
                    program=InterviewApplication(cap.session_id,cap.task_id,task['brief'],artifact_root=str(root.parent),model_route='deepseek',initial_model=self.model)
                    pending=program.advance()
                    for _ in range(100):
                        cap.validate(self.store)
                        if pending is None:
                            if not program.finished:raise ValueError('interview did not produce typed completion')
                            break
                        self.progress('interview.waiting','Waiting for founder input; no timeout')
                        response=self.answer(pending)
                        if not isinstance(response,InputResponse) or response.input_id!=pending.input_id or response.actor_id!=scope.owner_id:
                            raise ValueError('current exact founder input required')
                        pending=program.advance(response)
                    else:raise ValueError('interview bounded input count reached')
                program.checkpointer.conn.close()
                for phase in ('intent','conops'):
                    self.store.update_phase(tid,phase,'done')
            if not asyncio.run(worker.execute(job,interview)):raise ValueError('interview job already claimed')
            job=self.store.enqueue_worker(tid,'phase:needs',execution_options={'model_route':'deepseek:'+self.model});self.job_ids.append(job)
            def generate(cap,root):self.generate(cap,root)
            if not asyncio.run(self.worker().execute(job,generate)):raise ValueError('generator job already claimed')
            return tid

    def worker(self):
        from spec_generator.worker_runtime import JobRuntime
        from spec_generator.model_config import StageModel
        def runtime(cap,root):
            return JobRuntime({stage:StageModel('deepseek',self.model) for stage in ('needs','srs','architecture','items','tests','validation','review','repair','expert_opinion')},
                str(root/'private-llm-calls.jsonl'),lambda text:self.progress('model.progress','Working; private diagnostic recorded'))
        from spec_generator.telemetry import UsageLog
        return SameHostProducerWorker(self.store,self.base,gateway_factory=self.gateway,
            usage_log_factory=lambda cap,root:UsageLog(cap.session_id),runtime_factory=runtime,wire_client_factory=self.generator_clients)

    def generate(self,cap,root):
        from spec_generator.graph import build_graph,checkpoint_serde
        from spec_generator.cli import _make_persist,_atomic_write
        from spec_generator.telemetry import UsageLog
        from spec_generator.progress import ProgressBus
        from spec_generator.stop_publication import FencedCheckpointer,publication_guard
        from spec_generator.cooperative_run import cli_stop_binding
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.types import Command
        from vmodel_contracts import SystemBaseline,SpecManifest
        intent=json.loads((root/'interview/intent_model.json').read_text());conops=json.loads((root/'interview/conops.json').read_text())
        baseline=SystemBaseline.model_validate({'baseline_id':cap.session_id+'-greenfield','system_name':'Household Tasks','mode':'greenfield','repositories':[],'immutable_decisions':[]}).model_dump(mode='json')
        out=root/'spec';out.mkdir(exist_ok=False);(out/'specs').mkdir()
        _atomic_write(out/'system-baseline.json',json.dumps(baseline))
        bus=ProgressBus()
        phase_map={'items':'specs',**{p.generator_stage:p.key for p in PHASE_DEFINITIONS if p.generator_stage}}
        def progress(event):
            cap.validate(self.store);self.progress(event.stage+'.'+event.status,event.label)
            # ProgressBus deliberately swallows consumer errors. Durable phase state
            # is written below only after canonical contract validation succeeds.
        bus.subscribe(progress)
        fence,generation=cli_stop_binding();conn=sqlite3.connect(out/'checkpoints.sqlite',check_same_thread=False)
        try:
            saver=FencedCheckpointer(SqliteSaver(conn,serde=checkpoint_serde()),publication_guard(fence,generation))
            log=UsageLog(cap.session_id)
            graph=build_graph(log,checkpointer=saver,progress=bus,review=True,
                stop_fence=fence,stop_generation=generation,persist=_make_persist(intent,out,greenfield=True,conops_present=True))
            cfg={'configurable':{'thread_id':cap.session_id}}
            result=graph.invoke({'intent_model':intent,'conops':conops,'system_baseline':baseline},cfg)
            for _ in range(30):
                cap.validate(self.store);snapshot=graph.get_state(cfg)
                interrupts=[i.value for task in snapshot.tasks for i in task.interrupts]
                if not interrupts:
                    if snapshot.next or not result.get('bundle'):raise ValueError('full graph completion and bundle required')
                    break
                if len(interrupts)!=1:raise ValueError('one exact gate at a time required')
                review=interrupts[0];digest=hashlib.sha256(json.dumps(review,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                self.progress('gate.waiting','Waiting for exact reviewed decision; no timeout')
                durable_gate=None
                if hasattr(self.store,'wait_at_gate'):
                    durable_gate=self.store.wait_at_gate(cap.task_id,review['gate'])
                    if durable_gate is None:raise ValueError('explicit private closed gate required')
                    retained={'task':self.store.task(cap.task_id),'graph_review':review,'graph_review_sha256':digest,'test_only':True}
                    retained_digest=hashlib.sha256(json.dumps(retained,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                    self.store.retain_decision_review(cap.task_id,'gate:gate-'+review['gate'],retained_digest,retained)
                response=self.review(review,digest)
                if not isinstance(response,dict) or set(response)!={'review_sha256','decision'} or response['review_sha256']!=digest:
                    raise ValueError('stale/unreviewed gate decision')
                decision=response['decision']
                if not isinstance(decision,dict) or decision.get('action') not in ('accept','regenerate'):
                    raise ValueError('approve or founder change request required; never raw JSON replacement')
                if decision['action']=='regenerate' and not decision.get('feedback'):raise ValueError('founder guidance required')
                if durable_gate is not None:
                    self.store.decide_gate(cap.task_id,'gate-'+review['gate'],
                        'approve' if decision['action']=='accept' else 'redo',selected_option='Proceed' if decision['action']=='accept' else None,
                        steer=decision.get('feedback'),actor=cap.scope.owner_id,expected_revision=durable_gate['revision'])
                result=graph.invoke(Command(resume=decision),cfg)
            else:raise ValueError('bounded attended gate count reached')
            bundle=result['bundle'];manifest=SpecManifest.model_validate(bundle['_manifest'])
            _atomic_write(out/'manifest.json',manifest.model_dump_json(indent=2))
            _atomic_write(out/'traceability.json',json.dumps(bundle['traceability']))
            from spec_generator.render import render_agents_md
            from vmodel_contracts import ArchitectureOverview,ItemSpec,PunchListItem
            _atomic_write(out/'AGENTS.md',render_agents_md(ArchitectureOverview.model_validate(bundle['_architecture']),
                [ItemSpec.model_validate(i) for i in bundle['items']],manifest,[PunchListItem.model_validate(i) for i in bundle.get('punch_list',[])]))
            from artifact_receipts import inventory
            _,canonical=inventory(root)
            # The graph channels are intermediate assembly bodies. Close with
            # the validated serialized contracts used by private revision reads.
            graph.update_state(cfg,{key:canonical[name] for key,name in
                (('intent_model','interview/intent_model.json'),('conops','interview/conops.json'),
                 ('needs','spec/needs.json'),('srs','spec/srs.json'),('architecture','spec/architecture.json'))},as_node='assemble')
            for phase in PHASE_DEFINITIONS:
                if phase.executor=='generator':
                    state='approved' if phase.key in ('srs','architecture') and hasattr(self.store,'wait_at_gate') else 'done'
                    self.store.update_phase(cap.task_id,phase.key,state)
        finally:conn.close()
