"""Explicit recovered-run continuation. Never claims uninterrupted acceptance."""
import hashlib,json,os,asyncio
from pathlib import Path
from types import SimpleNamespace
from delivery_api.tenancy import TenantScope,tenant_context,session_root
from delivery_api.worker_capability import WorkerCapability
from isolation import RunIsolation
from artifact_receipts import inventory


def continuation_pipeline_receipt(isolation,pipeline,store,scope,task,prior_job,review,*,original_failed_jobs=None,pending_review=None):
    """Old failed job stays visible; only exact audited predecessor exempted."""
    from completion_adapters import EvidenceReader
    tid=pipeline.execute_existing(isolation,scope,task,prior_job=prior_job,review=review,pending_review=pending_review)
    cap=WorkerCapability.capture(store,tid);cap.validate(store)
    root=session_root(pipeline.base,scope,cap.session_id)
    files,documents=inventory(root)
    task=store.task(tid)
    if any(p['state'] not in ('done','approved') for p in task['phases']):raise ValueError('durable phase completion required')
    reader=EvidenceReader(store);jobs=reader.jobs(tid)
    failures=[j for j in jobs if j['state']!='done']
    expected=set(original_failed_jobs or [prior_job])
    if {j['job_id'] for j in failures}!=expected or any(j['state']!='failed' for j in failures):
        raise ValueError('only exact audited failed predecessor may remain')
    links=[e['detail']['new_job_id'] for e in store.audit_entries(tid) if e['action']=='item6.continuation.queued' and e['detail'].get('original_failed_job')==prior_job]
    linked=[j for j in jobs if j['job_id'] in links]
    if len(linked)!=1 or linked[0]['state']!='done':raise ValueError('one completed linked continuation required')
    proofs=[e for e in store.audit_entries(tid) if e['action']=='item6.provider.reconciled' and e['detail'].get('plan',{}).get('job_id') in expected]
    if not proofs:raise ValueError('original uncertain accounting reconciliation audit required')
    compute=store.retail_compute_status(tid)
    if compute.get('unresolved') or compute.get('pending'):raise ValueError('compute unresolved')
    usage=reader.usage(tid)
    if not usage or any(u['state']!='settled' or u.get('input_estimate_overrun') for u in usage):raise ValueError('usage not reconciled')
    return {'kind':'item6-continuation-pipeline','original_failed_job':prior_job,'failed_predecessor_job_ids':sorted(original_failed_jobs or [prior_job]),'task_id':tid,'session_id':cap.session_id,
      'artifact_sha256':files,'new_job_ids':pipeline.job_ids,'complete_canonical_pipeline':True,'uninterrupted_acceptance':False}


def check_quiescent_continuation(store,task_id,prior_job,*,original_failed_jobs=None):
    from completion_adapters import EvidenceReader
    reader=EvidenceReader(store)
    jobs=reader.jobs(task_id)
    expected=set(original_failed_jobs or [prior_job])
    if {j['job_id'] for j in jobs if j['state']=='failed'}!=expected:raise ValueError('audited failed predecessors changed')
    if any(j['state']!='done' and not(j['job_id'] in set(original_failed_jobs or [prior_job]) and j['state']=='failed') for j in jobs):
        raise ValueError('continuation jobs unresolved')
    if any(j['state']=='done' and j['attempt']!=1 for j in jobs):raise ValueError('new job replay detected')
    compute=store.retail_compute_status(task_id)
    if compute.get('pending') or compute.get('unresolved'):raise ValueError('compute unresolved')
    if any(u['state']!='settled' or u.get('input_estimate_overrun') for u in reader.usage(task_id)):raise ValueError('provider ledger unresolved')
    with store._postgres.transaction() as c:
        installations=c.execute('SELECT installation_id FROM closed_revision_installations WHERE task_id=%s',(task_id,)).fetchall()
    if not installations:raise ValueError('closed edit installation evidence required')
    # The caller validates installation file lineage with its artifact root.
    return {'kind':'item6-linked-continuation-quiescent','original_failed_job':prior_job,'failed_predecessor_job_ids':sorted(original_failed_jobs or [prior_job]),'unknown_attempts':0,
      'blind_replays':0,'uninterrupted_acceptance':False,'launch_authorized':False}


def execute_continuation(*,store,scope,root,config,run_id,prior_job,task_id,output,pipeline_factory,original_failed_jobs=None,pending_review=None):
    """Only caller with reviewed exact paid go may invoke. Does not authorize go."""
    output=Path(output)
    if output.exists() or output.is_symlink():raise ValueError('fresh continuation result directory required')
    output.mkdir(mode=0o700)
    isolation=SimpleNamespace(root=Path(root),run_id=run_id,mode='mac_acceptance')
    # New reviews live outside original review-001..020 names.
    from attended import Attended
    attended=Attended(output,lambda:scope.owner_id)
    with tenant_context(scope):
        if not store.auth_session_active():raise ValueError('active original restored auth session required')
        task=store.task(task_id)
        if not task:raise ValueError('original owned task required')
        cap=WorkerCapability.capture(store,task_id);cap.validate(store)
        session=session_root(Path(root),scope,cap.session_id)
        review=json.loads((session/'interview/semantic-review.json').read_text())
        pipeline=pipeline_factory(attended)
        receipt=continuation_pipeline_receipt(isolation,pipeline,store,scope,task,prior_job,review,original_failed_jobs=original_failed_jobs,pending_review=pending_review)
        RunIsolation.write(output/'pipeline.json',receipt)
        from revision_executor import PrivateGateEdit
        from completion_adapters import FinalArtifactsAdapter
        identity=SimpleNamespace(scope=scope)
        # Preserve original chain files; new evidence uses a separate root.
        stage_isolation=SimpleNamespace(root=output,run_id=run_id,mode='mac_acceptance')
        changed=PrivateGateEdit(identity,store=store,artifacts_base=Path(root),pipeline=pipeline,
          choose_change=attended.change,accept_fresh_review=attended.approve_revision)(stage_isolation,{'pipeline':receipt})
        RunIsolation.write(output/'gate_edit.json',changed)
        exported=FinalArtifactsAdapter(identity,store=store,artifacts_base=Path(root))(stage_isolation,{'pipeline':receipt,'gate_edit':changed})
        RunIsolation.write(output/'final_artifacts.json',exported)
        quiescent=check_quiescent_continuation(store,task_id,prior_job,original_failed_jobs=original_failed_jobs)
        with store._postgres.transaction() as c:
            installations=c.execute('SELECT installation_id FROM closed_revision_installations WHERE task_id=%s',(task_id,)).fetchall()
        for row in installations:
            proof=store.reconcile_private_revision_installation(task_id,installation_id=row['installation_id'],artifacts_base=Path(root))
            if proof['state']!='installed_verified_no_dispatch' or proof['unverified']:raise ValueError('continuation installation proof unresolved')
        with tenant_context(TenantScope('00000000-0000-0000-0000-000000000099','00000000-0000-0000-0000-000000000099')):
            if store.task(task_id) is not None or store.recoverable_worker_jobs():raise ValueError('unrelated identity saw continuation')
        quiescent['foreign_owner_denied']=True
        RunIsolation.write(output/'recovery.json',quiescent)
        result={'result':'CONTINUATION_EVIDENCE_READY','original_run_id':run_id,'original_failed_job':prior_job,'failed_predecessor_job_ids':sorted(original_failed_jobs or [prior_job]),
          'uninterrupted_acceptance':False,'original_acceptance_result':'INCOMPLETE','launch_authorized':False,'production_routes_enabled':False,
          'evidence_sha256':{n:hashlib.sha256((output/(n+'.json')).read_bytes()).hexdigest() for n in ('pipeline','gate_edit','final_artifacts','recovery')}}
        RunIsolation.write(output/'result.json',result)
        return result
