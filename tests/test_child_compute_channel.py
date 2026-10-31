from decimal import Decimal
from types import SimpleNamespace
import pytest
from requi_child_contract.compute import ComputeClient,measured_work,excluded_wait
from delivery_api.child_compute_channel import ChildComputeAccounting,child_compute_channel
from test_child_provider_broker import Journal
from test_retail_ledger import account
from test_child_execution_contract import operation,frame
from delivery_api.child_execution_contract import decode_operation

class Store:
    def __init__(self):self.calls=[]
    def begin_retail_compute(self,*args):self.calls.append(('begin',args))
    def finish_retail_compute(self,*args):self.calls.append(('finish',args))
    def close_failed_retail_compute(self,*args):self.calls.append(('abort',args))


def test_real_socket_monotonic_transcript_settles_exact_union_once():
    store=Store();account=ChildComputeAccounting(store,decode_operation(frame(operation())),rates=SimpleNamespace(snapshot=lambda:{}),phase='intent',journal=Journal())
    times=iter([0,10_000_000,20_000_000,30_000_000,40_000_000,50_000_000])
    with child_compute_channel(account) as channel:
        client=ComputeClient(channel.fileno(),clock=lambda:next(times))
        with measured_work(client):
            with excluded_wait('provider:a'):
                with excluded_wait('founder:b'):pass
        client.close()
    assert account.settled and store.calls[-1][0]=='finish'
    assert store.calls[-1][1][1]==Decimal(20)
    assert len(account.transcript)==6


def test_crash_missing_finish_never_prices_elapsed_wall_time():
    store=Store();account=ChildComputeAccounting(store,decode_operation(frame(operation())),rates=SimpleNamespace(snapshot=lambda:{}),phase='intent',journal=Journal())
    with child_compute_channel(account) as channel:
        client=ComputeClient(channel.fileno());client.emit('start');client.emit('wait.begin','provider:a');client.close()
    assert [call[0] for call in store.calls]==['begin','abort'] and not account.settled


def test_durability_failure_holds_compute_instead_of_reporting_success():
    journal=Journal();journal.fault='complete';store=Store()
    account=ChildComputeAccounting(store,decode_operation(frame(operation())),rates=SimpleNamespace(snapshot=lambda:{}),phase='intent',journal=journal)
    with child_compute_channel(account) as channel:
        client=ComputeClient(channel.fileno());client.emit('start')
        with pytest.raises(Exception):client.emit('finish')
        client.close()
    assert [call[0] for call in store.calls]==['begin','abort'] and not account.settled


def test_exact_child_compute_posts_once_to_real_ledger(account):
    import json,psycopg
    from delivery_api.child_broker_journal import ChildBrokerJournal
    from delivery_api.retail_pricing import RetailRates
    from delivery_api.tenancy import tenant_context
    from test_postgres_store import A,DSN
    store,tid,job=account;data=operation()
    data['binding'].update(owner_id=A.owner_id,auth_session_id=A.session_id,task_id=tid,job_id=job)
    with tenant_context(A):data['binding']['session_id']=store.worker_job(job)['session_id']
    op=decode_operation(frame(data));rates=RetailRates.from_environment({})
    with tenant_context(A):
        account=ChildComputeAccounting(store,op,rates=rates,phase='needs',journal=ChildBrokerJournal(store,op))
        times=iter([0,10_000_000])
        with child_compute_channel(account) as channel:
            client=ComputeClient(channel.fileno(),clock=lambda:next(times))
            with measured_work(client):pass
            client.close()
        assert account.settled
        before=store.retail_phase_totals(job)
        account.finish()
        assert store.retail_phase_totals(job)==before
        assert before['needs']['credits']==Decimal('0.0000005')
    with psycopg.connect(DSN) as conn:conn.execute('DELETE FROM staged_provider_responses WHERE session_id=%s',(op.binding.session_id,))


def test_gate_continuation_gets_distinct_compute_segment_and_evidence_key():
    first=decode_operation(frame(operation()))
    data=operation();data['binding']['lease_epoch']+=1
    second=decode_operation(frame(data));journal=Journal();store=Store()
    one=ChildComputeAccounting(store,first,rates=SimpleNamespace(snapshot=lambda:{}),phase='needs',journal=journal)
    two=ChildComputeAccounting(store,second,rates=SimpleNamespace(snapshot=lambda:{}),phase='needs',journal=journal)
    assert one.segment!=two.segment
    assert one.evidence_key!=two.evidence_key


def test_real_ledger_invocations_charge_separately_and_replay_cannot_reopen(account):
    import psycopg
    from delivery_api.child_broker_journal import ChildBrokerJournal
    from delivery_api.retail_pricing import RetailRates
    from delivery_api.tenancy import tenant_context
    from delivery_api.attempt_ledger import AdmissionDenied
    from test_postgres_store import A,DSN
    store,tid,job=account;data=operation();rates=RetailRates.from_environment({})
    data['binding'].update(owner_id=A.owner_id,auth_session_id=A.session_id,task_id=tid,job_id=job)
    with tenant_context(A):
        data['binding']['session_id']=store.worker_job(job)['session_id']
        accounts=[]
        for epoch in (2,3):
            data['binding']['lease_epoch']=epoch;op=decode_operation(frame(data))
            item=ChildComputeAccounting(store,op,rates=rates,phase='needs',journal=ChildBrokerJournal(store,op))
            with child_compute_channel(item) as channel:
                times=iter([0,10_000_000]);client=ComputeClient(channel.fileno(),clock=lambda:next(times))
                with measured_work(client):pass
                client.close()
            accounts.append(item)
        assert store.retail_phase_totals(job)['needs']['credits']==Decimal('0.000001')
        before=store.retail_phase_totals(job)
        accounts[0].finish();accounts[1].finish()
        replay=ChildComputeAccounting(store,op,rates=rates,phase='needs',journal=ChildBrokerJournal(store,op))
        with pytest.raises(AdmissionDenied,match='already begun'):replay.begin()
        assert store.retail_phase_totals(job)==before
        with store._postgres.transaction() as conn:
            assert conn.execute('SELECT count(*) AS n FROM retail_compute_segments WHERE job_id=%s',(job,)).fetchone()['n']==2
        assert all(item.journal.load(item.evidence_key)['response']['milliseconds']=='10' for item in accounts)
    with psycopg.connect(DSN) as conn:conn.execute('DELETE FROM staged_provider_responses WHERE session_id=%s',(op.binding.session_id,))
