"""Private durable proposal job for a reviewed closed-gate change.

No public route, live generation commit, artifact swap or restart. Draft Save is
unchanged. Proposal generation requires a separate explicit cost acknowledgement.
"""
from .worker_capability import WorkerCapability
from .gate_revision import closed_revision_preflight

KIND='proposal:closed-gate-revision'
KEYS={'phase','gate_key','gate_revision','review_revision','draft_id','preflight_digest',
      'source_checkpoint_id','change_request','artifact_evidence','extra_cost_acknowledged'}


def _options(options):
    import re
    if not isinstance(options,dict) or set(options)!=KEYS:raise ValueError('exact durable closed-revision options required')
    if options['phase'] not in ('srs','architecture') or options['gate_key']!='gate-'+options['phase']:
        raise ValueError('implemented closed-phase proposal adapter required')
    if type(options['gate_revision']) is not int or options['gate_revision']<0:
        raise ValueError('exact reviewed gate revision required')
    for key in ('review_revision','draft_id','source_checkpoint_id'):
        if not isinstance(options[key],str) or not 1<=len(options[key])<=200:raise ValueError('bounded reviewed identity required')
    if not isinstance(options['preflight_digest'],str) or not re.fullmatch('[a-f0-9]{64}',options['preflight_digest']):
        raise ValueError('reviewed preflight digest required')
    if not isinstance(options['change_request'],str) or not options['change_request'].strip() or len(options['change_request'])>20000:
        raise ValueError('explicit founder change required')
    from .phases import PHASE_BY_KEY
    evidence=options['artifact_evidence']
    if not isinstance(evidence,list) or not evidence:raise ValueError('durable artifact evidence required')
    names=set();generation=None
    for item in evidence:
        if not isinstance(item,dict) or set(item)!={'phase','name','generation','sha256'}:
            raise ValueError('exact durable artifact evidence required')
        if item['phase']!=options['phase'] or type(item['generation']) is not int or item['generation']<1:
            raise ValueError('artifact evidence phase/generation mismatch')
        if generation is not None and generation!=item['generation']:raise ValueError('mixed artifact generations')
        generation=item['generation']
        if not isinstance(item['name'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,199}',item['name']) or item['name'] in names:
            raise ValueError('canonical unique artifact evidence required')
        names.add(item['name'])
        if not isinstance(item['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',item['sha256']):
            raise ValueError('artifact content digest required')
    if not set(PHASE_BY_KEY[options['phase']].required_artifacts)<=names:raise ValueError('complete phase artifact evidence required')
    if options['extra_cost_acknowledged'] is not True:
        raise ValueError('explicit reviewed extra-cost acknowledgement required')
    return options


def queue_closed_revision_proposal(store,task_id,*,gate_key,review_revision,draft_id,
        reviewed_preflight_digest,source_checkpoint_id,artifact_snapshot,extra_cost_acknowledged):
    """Caller supplies fresh authorized evidence; enqueues proposal only.

    Must be called under the store's task-operation guard to serialize the
    current closed gate/draft basis with durable job creation. No source evidence
    supplied by an HTTP client is authenticated by this helper.
    """
    with store._task_operation_guard('task.rerun'):
        cap=WorkerCapability.capture(store,task_id);cap.validate(store)
        prepared=closed_revision_preflight(store,task_id,gate_key,review_revision,draft_id,
            artifact_snapshot=artifact_snapshot)
        if prepared['review_digest']!=reviewed_preflight_digest:raise ValueError('reviewed proposal preflight changed')
        phase=prepared['proposal']['phase']
        options=_options({'phase':phase,'gate_key':gate_key,'gate_revision':prepared['proposal']['gate_revision'],
            'review_revision':review_revision,'draft_id':draft_id,'preflight_digest':reviewed_preflight_digest,
            'source_checkpoint_id':source_checkpoint_id,'change_request':prepared['proposal']['edited_text'],
            'artifact_evidence':prepared['artifact_evidence'],'extra_cost_acknowledged':extra_cost_acknowledged})
        return store.enqueue_worker(task_id,KIND,execution_options=options)


async def execute_closed_revision_proposal(store,worker,job_id,*,read_artifact_evidence,log_factory,prepare=None):
    job=store.worker_job(job_id)
    if not job or job['kind']!=KIND:raise ValueError('durable closed-revision proposal job required')
    options=_options(job['execution_options']);proposals=[]
    def producer(capability,root):
        def authority():
            capability.validate(store)
            current=closed_revision_preflight(store,capability.task_id,options['gate_key'],options['review_revision'],
                options['draft_id'],artifact_snapshot=read_artifact_evidence(capability,root,options['phase']))
            if current['review_digest']!=options['preflight_digest']:
                raise ValueError('closed-revision evidence changed')
        authority()
        if prepare is None:
            from spec_generator.revision_worker import run_revision_proposal
            runner=run_revision_proposal
        else:runner=prepare
        proposals.append(runner(capability=capability,session_root=root,checkpoint_id=options['source_checkpoint_id'],
            phase=options['phase'],change_request=options['change_request'],validate_authority=authority,
            log=log_factory(capability,root)))
    started=await worker.execute(job_id,producer)
    return {'job_id':job_id,'started':started,'proposals':proposals,'source_committed':False,'restart_dispatched':False}
