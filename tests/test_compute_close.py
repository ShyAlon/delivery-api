"""A job that fails before any provider call must not leave compute accounting that blocks the owner's next Start.
Rules stay pure; close/release contracts use disposable restricted PostgreSQL."""
import re
from decimal import Decimal
from pathlib import Path

import pytest

from delivery_api import compute_segments as cs
from delivery_api.tenancy import tenant_context

ROOT=Path(__file__).resolve().parents[1]/'src'/'delivery_api'


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
    assert cs.releasable('unresolved','running',0,0) is False
    assert cs.releasable('settled','failed',0,0) is False


def test_release_decision_waives_a_crashed_job_whose_provider_calls_are_all_terminal():
    # The blocked-interview shape: job died after its provider calls settled
    # (charges already taken). The owner can release it; only in-flight calls hold.
    assert cs.release_decision('unresolved','failed',0,2,1)=='waive'
    assert cs.release_decision('unresolved','done',0,1,0)=='waive'
    assert cs.release_decision('unresolved','failed',0,0,0)=='void'
    assert cs.release_decision('pending','failed',0,0,0)=='void'
    assert cs.release_decision('unresolved','failed',1,2,1) is None   # a call still in flight: review
    assert cs.release_decision('unresolved','running',0,2,1) is None  # job alive: not the owner's call
    assert cs.release_decision('settled','failed',0,2,1) is None
    assert cs.releasable('unresolved','failed',2,1,attempts_nonterminal=0) is True
    assert cs.releasable('unresolved','failed',2,1,attempts_nonterminal=1) is False


@pytest.fixture
def compute_workspace(pg_workspace_factory):
    from uuid import uuid4
    from delivery_api.retail_pricing import RetailRates
    with pg_workspace_factory() as workspace,tenant_context(workspace.scope):
        store=workspace.store
        tid=store.create_task('Compute close')['task_id'];store.set_engine_session(tid,'compute-'+uuid4().hex)
        rates=RetailRates.from_environment({}).snapshot()
        with workspace.admin() as connection:
            connection.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,100000000000,1000000000,0)',(workspace.scope.owner_id,))
        def seed(n,state='pending',job_state='failed',attempts=0,nonterminal=0,tokens=0):
            job=store.enqueue_worker(tid,'fixture:'+str(n))
            assert store.claim_worker(job)
            key=job+':compute'
            store.begin_retail_compute(key,job,'intent',rates)
            with workspace.admin() as connection:
                connection.execute('UPDATE worker_jobs SET state=%s WHERE job_id=%s',(job_state,job))
                connection.execute('UPDATE retail_compute_segments SET state=%s,started_at=%s WHERE segment_key=%s',(state,n,key))
                for i in range(attempts):
                    connection.execute("INSERT INTO provider_attempts(attempt_id,owner_id,job_id,config_version,quote,fingerprint,state,expense_reserved,retail_reserved,created_at,updated_at) VALUES(%s,%s,%s,'fixture','{}','fixture',%s,1,1,0,0)",(uuid4().hex,workspace.scope.owner_id,job,'dispatched' if i<nonterminal else 'cancelled'))
                for i in range(tokens):
                    connection.execute("INSERT INTO retail_contributions VALUES(%s,%s,%s,'intent','tokens',1,0,0,'{}',1,1,1000000000,'fixture',0)",(uuid4().hex,workspace.scope.owner_id,job))
            return job,key
        def state(key):
            with workspace.admin() as connection:return connection.execute('SELECT state,deterministic_ms FROM retail_compute_segments WHERE segment_key=%s',(key,)).fetchone()
        def evidence():
            with workspace.admin() as connection:
                return (connection.execute('SELECT count(*),COALESCE(sum(exact_units),0) FROM retail_contributions WHERE owner_id=%s',(workspace.scope.owner_id,)).fetchone(),connection.execute('SELECT * FROM credit_accounts WHERE owner_id=%s',(workspace.scope.owner_id,)).fetchall())
        yield workspace,seed,state,evidence


def test_failed_job_with_no_provider_call_is_closed_without_charge_and_audited(compute_workspace):
    workspace,seed,state,evidence=compute_workspace;job,key=seed(1);before=evidence()
    assert workspace.store.close_failed_retail_compute(key)=='closed'
    assert state(key)==('settled',Decimal(0)) and evidence()==before
    audit=[a for a in workspace.store.audit_entries(None) if a['action']=='compute.segment_closed_no_provider_call'][0]
    assert audit['action']=='compute.segment_closed_no_provider_call'
    assert audit['actor']==workspace.scope.owner_id
    assert audit['detail']['charged'] is False and audit['detail']['provider_attempts']==0


def test_failed_job_that_reached_a_provider_stays_unresolved_for_review(compute_workspace):
    workspace,seed,state,evidence=compute_workspace;job,key=seed(1,attempts=1);before=workspace.store.audit_entries(None)
    assert workspace.store.close_failed_retail_compute(key)=='held'
    assert state(key)[0]=='unresolved' and workspace.store.audit_entries(None)==before


def test_closing_twice_and_closing_a_settled_or_missing_segment_change_nothing(compute_workspace):
    workspace,seed,state,evidence=compute_workspace;job,key=seed(1,state='settled',job_state='done');before=(state(key),evidence(),workspace.store.audit_entries(None))
    assert workspace.store.close_failed_retail_compute(key)=='settled'
    assert workspace.store.close_failed_retail_compute('nope')=='missing'
    assert (state(key),evidence(),workspace.store.audit_entries(None))==before


def _release_shape(seed):
    return [seed(1,'unresolved'),seed(2,'unresolved'),seed(3,'unresolved',attempts=2,nonterminal=1),seed(4,'pending',job_state='running'),seed(5,'unresolved',attempts=2,tokens=1)]


def test_release_preview_writes_nothing_and_lists_what_it_would_do(compute_workspace):
    workspace,seed,state,evidence=compute_workspace;rows=_release_shape(seed)
    before=([state(key) for _,key in rows],evidence(),workspace.store.audit_entries(None))
    out=workspace.store.release_own_compute(False)
    assert [i['job'] for i in out['releasable']]==[rows[i][0][:8] for i in (0,1,4)] and out['released']==0
    reasons={k['job']:k['reason'] for k in out['kept']}
    assert 'running' in reasons[rows[3][0][:8]] and 'still in flight' in reasons[rows[2][0][:8]]
    assert ([state(key) for _,key in rows],evidence(),workspace.store.audit_entries(None))==before


def test_release_closes_only_eligible_segments_each_audited_and_never_charges(compute_workspace):
    workspace,seed,state,evidence=compute_workspace;rows=_release_shape(seed);before=evidence();audits=workspace.store.audit_entries(None)
    out=workspace.store.release_own_compute(True)
    assert out['released']==3 and len(out['kept'])==2
    assert [state(key)[0] for _,key in rows]==['settled','settled','unresolved','pending','settled']
    known={a['id'] for a in audits}
    added=sorted((a for a in workspace.store.audit_entries(None) if a['id'] not in known),key=lambda a:a['id'])
    assert [a['action'] for a in added]==['compute.segment_released_by_owner']*2+['compute.segment_reconciled_no_charge']
    assert all(a['detail']['charged'] is False for a in added) and evidence()==before


def test_a_released_segment_no_longer_blocks_the_next_admission(compute_workspace):
    workspace,seed,state,evidence=compute_workspace;job,key=seed(1,'unresolved')
    workspace.store.release_own_compute(True)
    assert cs.compute_block(state(key)[0],'failed') is None


def test_a_job_with_no_row_is_not_running_and_is_releasable_when_it_made_no_call(compute_workspace):
    # The real PG FK prevents orphan segment rows. Keep this defensive rule
    # explicit without disabling constraints just to manufacture corrupt data.
    workspace,seed,state,evidence=compute_workspace
    with workspace.admin() as connection:
        assert connection.execute("SELECT count(*) FROM worker_jobs WHERE job_id='missing-job'").fetchone()[0]==0
    import psycopg
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with workspace.admin() as connection:
            connection.execute("INSERT INTO retail_compute_segments(segment_key,owner_id,job_id,phase_key,rates,state,started_at) VALUES('orphan',%s,'missing-job','intent','{}','unresolved',0)",(workspace.scope.owner_id,))
    assert cs.release_decision('unresolved',None,0,0,0)=='void'


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
