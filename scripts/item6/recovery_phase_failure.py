"""Exact audited no-provider successor failure. No synthetic compute settlement."""
from delivery_api.attempt_ledger import AdmissionDenied
from delivery_api.tenancy import current_scope
from recovery_accounting import waive_unknown_compute

JOB='22535ad84a5ddccf380a88bdddf23e50'
ARCHIVE='0be3e545e0669b85c3963dc40c53df6a809eab7a5295616c47058d2646873428'
AUTHORITY='wamid.HBgMOTcyNTQ1Njc4MzMwFQIAEhgUM0IxODY1OTlGRTk2QzgzMTZCMzEA'
ACTION='item6.compute.failure_waived'
ORIGINAL_JOBS={'edad945ddadfa63b5bd73d5546a5bf69':'failed','9e04190ef41fac9f64e5f75b96e60b77':'failed','2068b70d89a9f5d94296309707176822':'failed','53467a7e6d7551d4d56c0a6ff62abff7':'done'}


def validate_phase_failure(store,*,waived):
    """Allow only this exact additional predecessor with no provider attempts."""
    scope=current_scope()
    with store._postgres.transaction() as c:
        jobs=c.execute('SELECT job_id,state,kind,attempt,generation,session_id,auth_session_id FROM worker_jobs WHERE task_id=%s',('c11813e4a4b7',)).fetchall()
        if {r['job_id']:r['state'] for r in jobs}!={**ORIGINAL_JOBS,JOB:'failed'}:raise AdmissionDenied('exact no-provider failure job chain required')
        row=next(r for r in jobs if r['job_id']==JOB)
        if row['kind']!='phase:needs' or row['attempt']!=1 or row['generation']!=1 or str(row['session_id'])!='c8186df5-bfde-4499-86c3-a34823aa7f45' or str(row['auth_session_id'])!=scope.session_id:raise AdmissionDenied('exact scoped failed successor required')
        if c.execute('SELECT 1 FROM provider_attempts WHERE job_id=%s LIMIT 1',(JOB,)).fetchone():raise AdmissionDenied('no provider attempt permitted on phase-shape failure')
        if c.execute('SELECT 1 FROM retail_contributions WHERE job_id=%s LIMIT 1',(JOB,)).fetchone():raise AdmissionDenied('no retail debit permitted on phase-shape failure')
        account=c.execute('SELECT balance_units,units_per_credit FROM credit_accounts WHERE owner_id=%s',(scope.owner_id,)).fetchone()
        if not account or account['balance_units']!=5362835621 or account['units_per_credit']!=10**9:raise AdmissionDenied('unchanged topped-up balance required')
        segment=c.execute('SELECT state,deterministic_ms FROM retail_compute_segments WHERE segment_key=%s AND job_id=%s',(JOB+':compute',JOB)).fetchone()
        if not segment or segment['state']!=('waived' if waived else 'unresolved') or segment['deterministic_ms'] is not None:raise AdmissionDenied('exact unmeasured failure compute disposition required')
        proofs=c.execute("SELECT detail FROM audit_log WHERE task_id=%s AND action=%s AND detail::jsonb->>'segment_key'=%s",('c11813e4a4b7',ACTION,JOB+':compute')).fetchall()
        if waived:
            import json
            if len(proofs)!=1:raise AdmissionDenied('one exact fourth compute waiver audit required')
            proof=proofs[0]['detail'];proof=json.loads(proof) if isinstance(proof,str) else proof
            if proof.get('authorization_ref')!=AUTHORITY or proof.get('original_dump_sha256')!=ARCHIVE or proof.get('measurement')!='unknown' or proof.get('disposition')!='waived' or proof.get('billed_units')!=0 or proof.get('failed_job_unchanged') is not True:raise AdmissionDenied('exact approved fourth compute waiver evidence required')
        elif proofs:raise AdmissionDenied('unexpected prior phase failure waiver')
    return {'failed_job':JOB,'provider_attempts':0,'retail_debit_units':0,'compute_measurement':'unknown','compute_state':'waived' if waived else 'unresolved','balance_units':5362835621,'original_failed_job_unchanged':True}


def apply_phase_failure_waiver(store,*,archive_sha256,authorization_ref):
    if archive_sha256!=ARCHIVE or authorization_ref!=AUTHORITY:raise AdmissionDenied('exact owner authority and crash archive required')
    with store._postgres.transaction():
        validate_phase_failure(store,waived=False)
        result=waive_unknown_compute(store,JOB+':compute',original_dump_sha256=ARCHIVE,authorization_ref=AUTHORITY)
        validate_phase_failure(store,waived=True)
    return result
