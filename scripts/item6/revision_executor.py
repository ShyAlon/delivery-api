"""Private test-only closed Gate/Edit integration; never enables a public route."""
import asyncio,hashlib,json,sqlite3,uuid
from pathlib import Path
from delivery_api.tenancy import tenant_context,session_root
from delivery_api.worker_capability import WorkerCapability
from delivery_api.gate_revision import save_closed_revision,closed_revision_preflight
from delivery_api.closed_revision_job import queue_closed_revision_proposal,execute_closed_revision_proposal
from delivery_api.revision_evidence import read_revision_artifacts
from delivery_api.revision_acceptance_adapter import PrivateRevisionAcceptanceAdapter
from delivery_api.revision_install_coordinator import apply_private_installation
from private_adapters import base


class PrivateGateEdit:
    def __init__(self,identity,*,store,artifacts_base,pipeline,choose_change,accept_fresh_review):
        self.identity=identity;self.store=store;self.base=Path(artifacts_base);self.pipeline=pipeline
        self.choose_change=choose_change;self.accept=accept_fresh_review

    def __call__(self,isolation,previous):
        from spec_generator.graph import checkpoint_serde
        from langgraph.checkpoint.sqlite import SqliteSaver
        from spec_generator.stop_fence import StopFence
        from spec_generator.cooperative_run import cli_execution
        from spec_generator.telemetry import UsageLog
        with tenant_context(self.identity.scope):
            tid=previous['pipeline']['task_id'];cap=WorkerCapability.capture(self.store,tid);root=session_root(self.base,cap.scope,cap.session_id)
            before={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and p.suffix in ('.json','.md')}
            # Full run output is reviewed on this private test path. This does
            # not open a public gate or confer authority on arbitrary fixtures.
            task=self.store.task(tid)
            with self.store._postgres.transaction() as c:
                rows=c.execute("SELECT revision,payload FROM decision_reviews WHERE task_id=%s AND wait_id='gate:gate-srs' ORDER BY created_at DESC LIMIT 1",(tid,)).fetchone()
            if not rows:raise ValueError('retained exact source approval required')
            source_digest=rows['revision'];source_review=json.loads(rows['payload'])
            change=self.choose_change(source_review,source_digest)
            if not isinstance(change,dict) or set(change)!={'review_sha256','change_request','extra_cost_acknowledged'} or change['review_sha256']!=source_digest or change['extra_cost_acknowledged'] is not True:
                raise ValueError('exact source review, founder guidance and cost acknowledgement required')
            draft=save_closed_revision(self.store,tid,'gate-srs',source_digest,change['change_request'])
            evidence=read_revision_artifacts(self.store,cap,root,'srs',artifacts_base=self.base)
            preflight=closed_revision_preflight(self.store,tid,'gate-srs',source_digest,draft['draft_id'],artifact_snapshot=evidence)
            conn=sqlite3.connect(root/'spec/checkpoints.sqlite');saved=SqliteSaver(conn,serde=checkpoint_serde()).get_tuple({'configurable':{'thread_id':cap.session_id}});conn.close()
            if saved is None:raise ValueError('exact source graph checkpoint required')
            checkpoint=saved.config['configurable']['checkpoint_id']
            job=queue_closed_revision_proposal(self.store,tid,gate_key='gate-srs',review_revision=source_digest,draft_id=draft['draft_id'],
                reviewed_preflight_digest=preflight['review_digest'],source_checkpoint_id=checkpoint,artifact_snapshot=evidence,extra_cost_acknowledged=True)
            self.pipeline.job_ids.append(job)
            executed=asyncio.run(execute_closed_revision_proposal(self.store,self.pipeline.worker(),job,
                read_artifact_evidence=lambda c,r,phase:read_revision_artifacts(self.store,c,r,phase,artifacts_base=self.base),
                log_factory=lambda *args:UsageLog(cap.session_id)))
            if not executed['started'] or len(executed['proposals'])!=1:raise ValueError('one fresh validated proposal required')
            proposal=executed['proposals'][0];options=self.store.worker_job(job)['execution_options']
            current={'artifacts':read_revision_artifacts(self.store,cap,root,'srs',artifacts_base=self.base),
                'checkpoint_id':checkpoint,'proposal_sha256':proposal['artifact_sha256']}
            held=self.store.retain_closed_revision_restart_intent(tid,request_key=uuid.uuid4().hex,options=options,proposal=proposal,
                approval_reference='item6:'+isolation.run_id+':source-review:'+source_digest,read_current_evidence=lambda:current)
            proposal_path=root/'spec/revision-proposals'/proposal['proposal_id']/'proposal.json'
            planned=self.store.retain_closed_revision_lineage_plan(tid,intent_id=held['intent_id'],semantic_session_id=cap.session_id,
                new_execution_session_id='revised-'+uuid.uuid4().hex,retained_proposal={'proposal':proposal,'document_sha256':hashlib.sha256(proposal_path.read_bytes()).hexdigest()})
            lid=planned['lineage_id'];self.store.retain_revision_source_review(tid,lineage_id=lid,artifacts_base=self.base)
            event=self.store.append_closed_revision_preparation_event(tid,lineage_id=lid,expected_previous_sha256=None,state='prepared',evidence={'intent_id':held['intent_id'],'lineage_id':lid})
            with cli_execution(StopFence(root/'interview/stop-request.json'),UsageLog(cap.session_id)):
                staged=self.store.prepare_revision_staging(tid,lineage_id=lid,expected_previous_sha256=event['event_sha256'],artifacts_base=self.base)
            review=self.store.retain_revision_installation_review(tid,lineage_id=lid,expected_staged_event_sha256=staged['event_sha256'],artifacts_base=self.base)
            adapter=PrivateRevisionAcceptanceAdapter(self.store,tid,lineage_id=lid,staged_event_sha256=staged['event_sha256'],artifacts_base=self.base)
            stale_rejected=False
            try:adapter.submit({'review_sha256':'0'*64,'choice':'approve','change_request':'','extra_cost_acknowledged':True})
            except ValueError:stale_rejected=True
            if not stale_rejected:raise ValueError('stale basis accepted')
            accepted=self.accept(review)
            if accepted!={'review_sha256':review['review_sha256'],'choice':'approve','change_request':'','extra_cost_acknowledged':True}:
                raise ValueError('fresh same-phase acceptance required')
            receipt=adapter.submit(accepted);iid='item6-install-'+uuid.uuid4().hex
            intent=adapter.prepare_installation(receipt['acceptance_reference'],iid)
            with self.store._postgres.transaction() as c:
                authorized=c.execute('SELECT event_sha256 FROM closed_revision_install_events WHERE installation_id=%s ORDER BY sequence DESC LIMIT 1',(iid,)).fetchone()
            with cli_execution(StopFence(root/'interview/stop-request.json'),UsageLog(cap.session_id)):
                installed=apply_private_installation(self.store,tid,installation_id=iid,expected_authorized_event_sha256=authorized['event_sha256'],artifacts_base=self.base,validate_acceptance=self.store.verify_private_revision_acceptance)
            if not installed['installed'] or installed['dispatch_started']:raise ValueError('verified private promotion without dispatch required')
            observed=self.store.reconcile_private_revision_installation(tid,installation_id=iid,artifacts_base=self.base)
            if observed['state']!='installed_verified_no_dispatch' or observed['unverified']:raise ValueError('installed lineage proof required')
            # Explicit phase-by-phase continuation, each new output reviewed before
            # the next paid job. Nothing auto-approves downstream stale artifacts.
            from delivery_api.revision_continuation import prepare_private_approved_continuation
            from delivery_api.revision_continuation_worker import queue_private_approved_continuation,execute_private_approved_continuation
            from delivery_api.revision_next_review import read_private_next_review,approve_private_next_review,prepare_private_successor,queue_private_successor,execute_private_successor
            later=WorkerCapability.capture(self.store,tid);promoted=session_root(self.base,later.scope,later.session_id)
            with cli_execution(StopFence(promoted/'interview/stop-request.json',execution_anchor=later.execution_anchor),UsageLog(later.session_id)):
                prepare_private_approved_continuation(self.store,tid,installation_id=iid,artifacts_base=self.base)
            next_job=queue_private_approved_continuation(self.store,tid,installation_id=iid,artifacts_base=self.base);self.pipeline.job_ids.append(next_job)
            continued=asyncio.run(execute_private_approved_continuation(self.store,self.pipeline.worker(),next_job,installation_id=iid,artifacts_base=self.base))
            if not continued['started']:raise ValueError('new exact continuation required')
            reference=None
            for expected_phase in ('architecture','specs','tests','validation'):
                shown=read_private_next_review(self.store,tid,installation_id=iid,artifacts_base=self.base,result_reference=reference)
                if shown['review']['phase']!=expected_phase:raise ValueError('exact next phase review required')
                decision=self.accept(shown)
                if decision!={'review_sha256':shown['review_sha256'],'choice':'approve','change_request':'','extra_cost_acknowledged':True}:
                    raise ValueError('explicit fresh downstream review required')
                approval_reference='item6:'+isolation.run_id+':next:'+shown['review_sha256']
                self.store.retain_decision_review(tid,'item6-attended-approval',shown['review_sha256'],{'decision':decision,'approval_reference':approval_reference})
                def verify_submission(review,sha,selected,ack):
                    retained=self.store.decision_review(tid,'item6-attended-approval',sha)
                    if retained!={'decision':decision,'approval_reference':approval_reference}:raise ValueError('attended submission changed')
                    return approval_reference
                approve_private_next_review(self.store,tid,installation_id=iid,artifacts_base=self.base,
                    review_sha256=shown['review_sha256'],extra_cost_acknowledged=True,selected_option='Approve',validate_submission=verify_submission,result_reference=reference)
                if expected_phase=='validation':break
                digest=shown['review_sha256']
                with cli_execution(StopFence(promoted/'interview/stop-request.json',execution_anchor=later.execution_anchor),UsageLog(later.session_id)):
                    prepare_private_successor(self.store,tid,installation_id=iid,artifacts_base=self.base,review_sha256=digest)
                next_job=queue_private_successor(self.store,tid,installation_id=iid,artifacts_base=self.base,review_sha256=digest);self.pipeline.job_ids.append(next_job)
                continued=asyncio.run(execute_private_successor(self.store,self.pipeline.worker(),next_job,installation_id=iid,artifacts_base=self.base,review_sha256=digest))
                if not continued['started']:raise ValueError('new exact successor required')
                reference=digest
            if self.store.task(tid)['state']!='done':raise ValueError('revised chain did not complete')
            if any(hashlib.sha256((root/name).read_bytes()).hexdigest()!=digest for name,digest in before.items()):raise ValueError('original canonical source changed')
            return base(isolation,'gate_edit')|{'task_id':tid,'owner_id':cap.scope.owner_id,
                'source_review_sha256':source_digest,'fresh_review_sha256':review['review_sha256'],'stale_rejected':True,
                'same_phase_reapproval':True,'source_preserved':True,'installed_lineage_verified':True}
