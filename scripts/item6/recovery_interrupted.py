"""Exact interrupted choice/ledger preflight. No provider contact or mutations."""
import json,sqlite3,shutil,tempfile
from pathlib import Path
ORIGINAL='edad945ddadfa63b5bd73d5546a5bf69'
INTERRUPTED='9e04190ef41fac9f64e5f75b96e60b77'
OWNER='045bafdf-e35b-4b4b-93f2-0cd981b52806'
SESSION='c8186df5-bfde-4499-86c3-a34823aa7f45'

def validate_interrupted_ledger(store):
    with store._postgres.transaction() as c:
        jobs=c.execute('SELECT job_id,state FROM worker_jobs WHERE task_id=%s',('c11813e4a4b7',)).fetchall()
        if {r['job_id'] for r in jobs}!={ORIGINAL,INTERRUPTED} or any(r['state']!='failed' for r in jobs):raise ValueError('two exact failed predecessor jobs required')
        rows=c.execute('SELECT * FROM provider_attempts').fetchall()
        if len(rows)!=76 or any(r['state']!='settled' or r.get('input_estimate_overrun') for r in rows):raise ValueError('exact76 settled attempts, no holds required')
        new=[r for r in rows if r['job_id']!=ORIGINAL]
        if len(new)!=10 or any(r['job_id']!=INTERRUPTED for r in new) or sum(r['expense_actual'] for r in new)!=5264388000:raise ValueError('interrupted paid expense ledger changed')
        segments=c.execute('SELECT job_id,state,deterministic_ms FROM retail_compute_segments').fetchall()
        if len(segments)!=2 or {r['job_id']:r['state'] for r in segments}!={ORIGINAL:'waived',INTERRUPTED:'unresolved'} or any(r['deterministic_ms'] is not None for r in segments):raise ValueError('exact original waived and interrupted unknown compute required')
        account=c.execute('SELECT balance_units,units_per_credit FROM credit_accounts').fetchone()
        if account['balance_units']!=3195487500 or account['units_per_credit']!=1000000000:raise ValueError('reviewed continuation balance changed')
    audits=store.audit_entries('c11813e4a4b7')
    if not any(e['action']=='item6.continuation.queued' and e['detail'].get('original_failed_job')==ORIGINAL and e['detail'].get('new_job_id')==INTERRUPTED for e in audits):raise ValueError('original linked continuation audit required')
    if not any(e['action']=='item6.provider.reconciled' for e in audits):raise ValueError('original reconciliation audit required')
    return {'settled_attempts':76,'additional_provider_actual_units':5264388000,'remaining_provider_cap_units':994735612000,'balance_units':3195487500}

def validate_pending_files(root,pending):
    from types import SimpleNamespace
    from recovery_checkpoint import restore_pending_concept
    root=Path(root)/'tenants'/OWNER/'sessions'/SESSION/'interview'
    review=json.loads((root/'semantic-review.json').read_text())
    with tempfile.TemporaryDirectory() as t:
        for name in ('checkpoints.sqlite','checkpoints.sqlite-wal','checkpoints.sqlite-shm'):
            p=root/name
            if p.exists():shutil.copy2(p,Path(t)/name)
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        from vmodel_contracts import AppArchetype
        from interview_engine.application import InterviewApplication
        with sqlite3.connect(Path(t)/'checkpoints.sqlite') as c:
            saver=SqliteSaver(c,serde=JsonPlusSerializer(allowed_msgpack_modules=[AppArchetype]));saver.is_setup=True
            saved=saver.get_tuple({'configurable':{'thread_id':SESSION}})
            if saved is None or saved.config['configurable']['checkpoint_id']!='1f1c56dc-1bcd-603e-8055-58e3c2a3c814':raise ValueError('exact interrupted checkpoint required')
            program=SimpleNamespace(session_id=SESSION,cfg={},graph=SimpleNamespace(get_state=lambda cfg:SimpleNamespace(values=saved.checkpoint['channel_values'],next=())),_decision_context=None)
            program._decision_context=lambda purpose:InterviewApplication._decision_context(program,purpose)
            binding=restore_pending_concept(program,review,json.loads(Path(pending).read_text()),actor=OWNER)
    return binding
