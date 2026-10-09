"""Private SRS rework job preparation/execution. No public route or auto-apply."""
from .worker_capability import WorkerCapability
from .tenancy import TenantAccessDenied

KIND='proposal:srs-rework'
KEYS={'gate_revision','source_checkpoint_id','source_review_revision','item_ids','guidance'}


def _request(options):
    import re
    if not isinstance(options,dict) or set(options)!=KEYS:raise ValueError('explicit SRS rework job options required')
    if not isinstance(options['source_checkpoint_id'],str) or not 1<=len(options['source_checkpoint_id'])<=200:
        raise ValueError('bounded source checkpoint identity required')
    if not isinstance(options['source_review_revision'],str) or not re.fullmatch('[a-f0-9]{64}',options['source_review_revision']):
        raise ValueError('source semantic revision required')
    if type(options['gate_revision']) is not int or options['gate_revision']<0:raise ValueError('exact queued gate revision required')
    ids=options['item_ids']
    if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(x,str) or not 1<=len(x)<=100 for x in ids) or len(set(ids))!=len(ids):
        raise ValueError('explicit unique selected requirements required')
    if not isinstance(options['guidance'],str) or len(options['guidance'])>7000:raise ValueError('bounded rework guidance required')
    return options


def queue_proposal(store,task_id,*,source_checkpoint_id,source_review_revision,item_ids,guidance):
    capability=WorkerCapability.capture(store,task_id);task=capability.validate(store)
    if task.get('stop_requested_at') is not None:raise TenantAccessDenied('stopped task rework disabled')
    gate=next((g for g in task['gates'] if g['after_phase']=='srs'),None)
    if not gate or gate['state']!='waiting':raise ValueError('waiting SRS gate required')
    options=_request(dict(gate_revision=gate['revision'],source_checkpoint_id=source_checkpoint_id,source_review_revision=source_review_revision,item_ids=list(item_ids),guidance=guidance))
    return store.enqueue_worker(task_id,KIND,execution_options=options)


async def execute_proposal(store,worker,job_id,*,log_factory,progress_factory=None,prepare=None):
    """Use only durable exact options, never replace selected items at dispatch."""
    job=store.worker_job(job_id)
    if not job:raise KeyError(job_id)
    if job['kind']!=KIND:raise ValueError('SRS rework proposal job required')
    options=_request(job['execution_options']);results=[]
    def producer(capability,root):
        task=capability.validate(store)
        gate=next((g for g in task['gates'] if g['after_phase']=='srs'),None)
        if not gate or gate['state']!='waiting' or gate['revision']!=options['gate_revision']:
            raise ValueError('SRS gate changed before producer call')
        expected_revision=gate['revision']
        def validate_publication():
            live=capability.validate(store)
            current=next((g for g in live['gates'] if g['after_phase']=='srs'),None)
            if not current or current['state']!='waiting' or current['revision']!=expected_revision:
                raise ValueError('SRS gate changed during producer call')
        from spec_generator.srs_rework import retain_checkpoint_proposal
        results.append(retain_checkpoint_proposal(capability=capability,session_root=root,
            checkpoint_id=options['source_checkpoint_id'],revision=options['source_review_revision'],
            item_ids=options['item_ids'],guidance=options['guidance'],log=log_factory(capability,root),
            progress=progress_factory(capability,root) if progress_factory else None,prepare=prepare,
            validate_publication=validate_publication))
    started=await worker.execute(job_id,producer)
    return {'job_id':job_id,'started':started,'proposals':results,'source_committed':False,'dispatch_started':False}
