"""Private authenticated submission adapter, not a route or install command.

Backend caller binds current owner/task/lineage/stage from authenticated state.
Browser supplies only choice/guidance/exact displayed digest/cost ack. No live
entry point installs this adapter; fixtures are not owner authority.
"""
class PrivateRevisionAcceptanceAdapter:
    def __init__(self,store,task_id,*,lineage_id,staged_event_sha256,artifacts_base):
        self.store,self.task_id=store,task_id
        self.basis={'lineage_id':lineage_id,'expected_staged_event_sha256':staged_event_sha256,'artifacts_base':artifacts_base}

    def submit(self,payload):
        if not isinstance(payload,dict) or set(payload)!={'review_sha256','choice','change_request','extra_cost_acknowledged'}:
            raise ValueError('exact private review submission required')
        common=self.basis|{'review_sha256':payload['review_sha256'],'extra_cost_acknowledged':payload['extra_cost_acknowledged']}
        if payload['choice']=='approve':
            if payload['change_request']!='':raise ValueError('approve must not carry edit guidance')
            result=self.store.record_private_revision_acceptance(self.task_id,**common,decision='approve')
            return {'review_sha256':payload['review_sha256'],'pending':'installation',
                'acceptance_reference':result['acceptance_reference'],'installed':False,'dispatch_started':False}
        if payload['choice']=='edit':
            result=self.store.prepare_private_revision_changes(self.task_id,**common,change_request=payload['change_request'])
            return {'review_sha256':payload['review_sha256'],'pending':'regeneration','submission_id':result['submission_id'],
                'same_phase_review_required':True,'installed':False,'dispatch_started':False}
        raise ValueError('approve or edit choice required')

    def queue_submitted_changes(self,submission_id):
        """Explicit private orchestration step; no route or automatic polling.

        Does not authorize a real run. Caller separately establishes owner scope
        to incur cost. Reads retained submission, never accepts browser options.
        """
        from .closed_revision_job import queue_closed_revision_proposal
        with self.store._task_operation_guard('task.rerun'):
            review=self.store.retain_revision_installation_review(self.task_id,**self.basis)
            submitted=self.store.decision_draft(self.task_id,'revision-changes:'+self.basis['lineage_id'],review['review_sha256'],submission_id)
            if not submitted or submitted.get('decision')!='edit' or submitted.get('extra_cost_acknowledged') is not True:
                raise ValueError('exact retained same-phase change submission required')
            options=submitted['options']
            return queue_closed_revision_proposal(self.store,self.task_id,gate_key=options['gate_key'],review_revision=options['review_revision'],
                draft_id=options['draft_id'],reviewed_preflight_digest=options['preflight_digest'],source_checkpoint_id=options['source_checkpoint_id'],
                artifact_snapshot=options['artifact_evidence'],extra_cost_acknowledged=options['extra_cost_acknowledged'])

    def prepare_installation(self,acceptance_reference,installation_id):
        """Retain exact private intent using concrete DB receipt verifier only."""
        with self.store._task_operation_guard('task.rerun'):
            review=self.store.retain_revision_installation_review(self.task_id,**self.basis)
            return self.store.retain_private_installation_intent(self.task_id,**self.basis,review_sha256=review['review_sha256'],
                installation_id=installation_id,acceptance_reference=acceptance_reference,
                validate_acceptance=self.store.verify_private_revision_acceptance)

    def save_draft(self,payload):
        if not isinstance(payload,dict) or set(payload)!={'review_sha256','change_request'}:
            raise ValueError('exact private draft submission required')
        text=payload['change_request']
        if not isinstance(text,str) or len(text)>20000:raise ValueError('bounded draft guidance required')
        with self.store._task_operation_guard('task.decision.draft'):
            review=self.store.retain_revision_installation_review(self.task_id,**self.basis)
            if review['review_sha256']!=payload['review_sha256']:raise ValueError('draft review is stale')
            draft_id=self.store.save_decision_draft(self.task_id,'revision-input:'+self.basis['lineage_id'],payload['review_sha256'],
                {'change_request':text,'submitted':False,'installed':False})
            return {'draft_id':draft_id,'review_sha256':payload['review_sha256'],'installed':False}
