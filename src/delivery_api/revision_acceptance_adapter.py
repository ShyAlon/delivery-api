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
