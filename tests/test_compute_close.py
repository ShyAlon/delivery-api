"""A job that fails before any provider call must not leave compute accounting that blocks the owner's next Start.
Rules and the close/release functions run against canned rows, no Postgres needed. See test_compute_close_pg.py for the real database."""
import re
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest

from delivery_api import compute_segments as cs
from delivery_api import retail_ledger
from delivery_api.tenancy import TenantScope,tenant_context

ROOT=Path(__file__).resolve().parents[1]/'src'/'delivery_api'
A='11111111-1111-4111-8111-111111111111'


def test_unresolved_always_blocks_and_pending_blocks_only_when_orphaned():
    assert cs.compute_block('unresolved','failed')=='unresolved'
    assert cs.compute_block('unresolved','running')=='unresolved'
    assert cs.compute_block('pending','running') is None
    assert cs.compute_block('pending','failed') and cs.compute_block('pending','done')
    assert cs.compute_block('settled','failed') is None
    assert cs.compute_block('pending',None) is None   # same as the admission SQL: no job row, no block


def test_decision_voids_only_with_no_provider_call_and_no_token_charge():
    assert cs.close_decision(0,0)=='void'
    assert cs.close_decision(1,0)=='hold' and cs.close_decision(0,1)=='hold' and cs.close_decision(3,2)=='hold'


def test_release_needs_an_ended_job_with_no_provider_call():
    assert cs.releasable('unresolved','failed',0,0) is True
    assert cs.releasable('pending','failed',0,0) is True
    assert cs.releasable('unresolved','failed',1,0) is False
    assert cs.releasable('unresolved','running',0,0) is False
    assert cs.releasable('settled','failed',0,0) is False


class Store:
    """Rows keyed by what the statement reads; records every write and audit entry."""
    def __init__(self,segments,job_states,attempts=None,tokens=None):
        self.segments={s['segment_key']:dict(s) for s in segments}
        self.job_states,self.attempts,self.tokens=job_states,attempts or {},tokens or {}
        self.writes,self.audits=[],[]
        store=self
        class Db:
            def execute(self,sql,params=()):
                class R:
                    def fetchone(s):
                        if 'pg_advisory_xact_lock' in sql:return {}
                        if 'FROM retail_compute_segments WHERE segment_key=?' in sql:return store.segments.get(params[0])
                        if 'FROM worker_jobs' in sql:
                            st=store.job_states.get(params[0]);return {'state':st} if st else None
                        if 'FROM provider_attempts' in sql:return {'n':store.attempts.get(params[0],0)}
                        if "kind='tokens'" in sql:return {'n':store.tokens.get(params[0],0)}
                    def fetchall(s):
                        if 'FROM retail_compute_segments WHERE owner_id=?' in sql:
                            assert params==(A,);return [r for r in store.segments.values() if r['state']!='settled']
                if sql.lstrip().upper().startswith(('UPDATE','INSERT')):
                    store.writes.append((sql,params))
                    if 'SET state=\'settled\'' in sql:store.segments[params[2]].update(state='settled',deterministic_ms=params[0])
                    if "SET state='unresolved'" in sql:store.segments[params[0]]['state']='unresolved'
                return R()
        self._db=Db()
    def audit(self,actor,action,task_id=None,detail=None):self.audits.append((actor,action,task_id,detail))


def seg(n,state='pending'):
    return {'segment_key':'job%d:compute'%n,'owner_id':A,'job_id':'job%d'%n,'phase_key':'intent','state':state}


def test_failed_job_with_no_provider_call_is_closed_without_charge_and_audited():
    s=Store([seg(1)],{'job1':'failed'})
    with tenant_context(TenantScope(A)):
        assert retail_ledger.close_failed_compute(s,'job1:compute')=='closed'
    assert s.segments['job1:compute']['state']=='settled' and s.segments['job1:compute']['deterministic_ms']==Decimal(0)
    assert not [w for w in s.writes if 'INSERT' in w[0].upper()]          # no contribution, so no debit
    assert s.audits[0][1]=='compute.segment_closed_no_provider_call' and s.audits[0][3]['charged'] is False and s.audits[0][3]['provider_attempts']==0
    assert s.audits[0][0]==A


def test_failed_job_that_reached_a_provider_stays_unresolved_for_review():
    s=Store([seg(1)],{'job1':'failed'},attempts={'job1':1})
    with tenant_context(TenantScope(A)):
        assert retail_ledger.close_failed_compute(s,'job1:compute')=='held'
    assert s.segments['job1:compute']['state']=='unresolved' and s.audits==[]


def test_closing_twice_and_closing_a_settled_or_missing_segment_change_nothing():
    s=Store([seg(1,'settled')],{'job1':'done'})
    with tenant_context(TenantScope(A)):
        assert retail_ledger.close_failed_compute(s,'job1:compute')=='settled'
        assert retail_ledger.close_failed_compute(s,'nope')=='missing'
    assert s.writes==[] and s.audits==[]


def test_release_preview_writes_nothing_and_lists_what_it_would_do():
    s=Store([seg(1,'unresolved'),seg(2,'unresolved'),seg(3,'unresolved'),seg(4,'pending')],
            {'job1':'failed','job2':'failed','job3':'failed','job4':'running'},attempts={'job3':2})
    with tenant_context(TenantScope(A)):
        out=retail_ledger.release_compute(s,A,False)
    assert [i['job'] for i in out['releasable']]==['job1','job2'] and out['released']==0 and s.writes==[] and s.audits==[]
    reasons={k['job']:k['reason'] for k in out['kept']}
    assert 'running' in reasons['job4'] and 'reached a provider' in reasons['job3']


def test_release_closes_only_eligible_segments_each_audited_and_never_charges():
    s=Store([seg(1,'unresolved'),seg(2,'unresolved'),seg(3,'unresolved'),seg(4,'pending')],
            {'job1':'failed','job2':'failed','job3':'failed','job4':'running'},attempts={'job3':2})
    with tenant_context(TenantScope(A)):
        out=retail_ledger.release_compute(s,A,True)
    assert out['released']==2 and len(out['kept'])==2
    assert {k:v['state'] for k,v in s.segments.items()}=={'job1:compute':'settled','job2:compute':'settled','job3:compute':'unresolved','job4:compute':'pending'}
    assert [a[1] for a in s.audits]==['compute.segment_released_by_owner']*2 and not [w for w in s.writes if 'INSERT' in w[0].upper()]


def test_a_released_segment_no_longer_blocks_the_next_admission():
    s=Store([seg(1,'unresolved')],{'job1':'failed'})
    with tenant_context(TenantScope(A)):
        retail_ledger.release_compute(s,A,True)
    assert cs.compute_block(s.segments['job1:compute']['state'],'failed') is None


def test_a_job_with_no_row_is_not_running_and_is_releasable_when_it_made_no_call():
    s=Store([seg(1,'unresolved')],{})
    with tenant_context(TenantScope(A)):
        assert retail_ledger.release_compute(s,A,False)['releasable'][0]['job']=='job1'


def test_the_producer_closes_by_evidence_instead_of_parking_every_failure():
    src=(ROOT/'same_host_producer.py').read_text()
    block=src.split('except BaseException:')[1].split('raise')[0]
    assert 'self.store.close_failed_retail_compute(segment)' in block and 'finish_retail_compute(segment,None)' not in block


def test_store_exposes_the_close_and_release_operations_with_the_owner_scope():
    src=(ROOT/'postgres_store.py').read_text()
    assert 'def close_failed_retail_compute(self,key)' in src and 'release_compute(self,current_scope().owner_id,bool(confirm))' in src


def test_delete_keeps_accounting_rows_by_hiding_the_task_instead_of_deleting_its_jobs():
    src=(ROOT/'postgres_store.py').read_text()
    body=src.split('def delete_task(')[1].split('def set_engine_session')[0]
    keep=body.rindex('accounting_retained')
    assert 'retail_compute_segments' in body[:keep] and 'retail_contributions' in body[:keep]
    assert body.index("UPDATE tasks SET deleted_at")<keep<body.index("for table in ('worker_jobs'")
