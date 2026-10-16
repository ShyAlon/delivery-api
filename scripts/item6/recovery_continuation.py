"""Explicit recovered-run continuation. Never claims uninterrupted acceptance."""
import hashlib,json,os,asyncio
from pathlib import Path
from types import SimpleNamespace
from delivery_api.tenancy import TenantScope,tenant_context,session_root
from delivery_api.worker_capability import WorkerCapability
from isolation import RunIsolation
from artifact_receipts import inventory


def require_reconciled_usage(store,task_id,usage,failed_jobs):
    """Only a named audited failure may replace unknown usage, never zero usage."""
    for u in usage:
        if u.get('input_estimate_overrun'):raise ValueError('input estimate overrun')
        if u['state']=='settled':continue
        if u['state']!='failure_waived' or u.get('job_id') not in failed_jobs:raise ValueError('provider ledger unresolved')
        proofs=[e['detail'] for e in store.audit_entries(task_id) if e['action']=='item6.provider.unknown_failure_waived' and e['detail'].get('attempt_id')==u['attempt_id']]
        if len(proofs)!=1 or proofs[0].get('failed_job')!=u['job_id'] or proofs[0].get('provider_expense_actual') is not None or proofs[0].get('conservative_cap_consumption_units')!=u['expense_reserved'] or not proofs[0].get('authorization_ref') or not proofs[0].get('balance_unchanged'):raise ValueError('exact unknown failure audit required')
        if any(u.get(k) is not None for k in ('usage','raw_usage','expense_actual','retail_actual','settled_at')):raise ValueError('waived unknown cannot be measured settlement')


def continuation_pipeline_receipt(isolation,pipeline,store,scope,task,prior_job,review,*,original_failed_jobs=None,pending_review=None,generator_resume=None):
    """Old failed job stays visible; only exact audited predecessor exempted."""
    from completion_adapters import EvidenceReader
    if generator_resume is not None:
        tid=pipeline.execute_generator_existing(isolation,scope,task,prior_job=prior_job,generator_resume=generator_resume)
    else:
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
    linked=[j for j in jobs if j['job_id'] in links and j['state']=='done']  # earlier failed resume jobs stay visible as failed
    if len(linked)!=1 or linked[0]['state']!='done':raise ValueError('one completed linked continuation required')
    proofs=[e for e in store.audit_entries(tid) if e['action']=='item6.provider.reconciled' and e['detail'].get('plan',{}).get('job_id') in expected]
    if not proofs:raise ValueError('original uncertain accounting reconciliation audit required')
    compute=store.retail_compute_status(tid)
    if compute.get('unresolved') or compute.get('pending'):raise ValueError('compute unresolved')
    usage=reader.usage(tid)
    if not usage:raise ValueError('usage evidence required')
    require_reconciled_usage(store,tid,usage,expected)
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
    require_reconciled_usage(store,task_id,reader.usage(task_id),expected)
    with store._postgres.transaction() as c:
        installations=c.execute('SELECT installation_id FROM closed_revision_installations WHERE task_id=%s',(task_id,)).fetchall()
    if not installations:raise ValueError('closed edit installation evidence required')
    # The caller validates installation file lineage with its artifact root.
    return {'kind':'item6-linked-continuation-quiescent','original_failed_job':prior_job,'failed_predecessor_job_ids':sorted(original_failed_jobs or [prior_job]),'unknown_attempts':0,
      'blind_replays':0,'uninterrupted_acceptance':False,'launch_authorized':False}


def execute_continuation(*,store,scope,root,config,run_id,prior_job,task_id,output,pipeline_factory,original_failed_jobs=None,pending_review=None,generator_resume=None):
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
        # Only the older edit-resume path uses the semantic review; a generator resume never has one.
        review=None if generator_resume is not None else json.loads((session/'interview/semantic-review.json').read_text())
        pipeline=pipeline_factory(attended)
        receipt=continuation_pipeline_receipt(isolation,pipeline,store,scope,task,prior_job,review,original_failed_jobs=original_failed_jobs,pending_review=pending_review,generator_resume=generator_resume)
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
