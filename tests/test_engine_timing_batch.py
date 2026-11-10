"""A timing chunk commits together without waiting for the answer to finish."""
import asyncio,sys,uuid
from pathlib import Path
import pytest
from test_postgres_store import store,A,B
from delivery_api import postgres_scope
from delivery_api.tenancy import tenant_context
from delivery_api.child_supervisor import ChildSupervisor,ChildRunError
from test_child_execution_contract import operation,frame
from delivery_api.child_execution_contract import decode_operation


def test_eight_timings_one_statement_transaction_ordered_and_owner_scoped(store):
    sid='timing-'+uuid.uuid4().hex
    with tenant_context(A):
        tid=store.create_task('timing-batch')['task_id'];store.set_engine_session(tid,sid)
        entries=[(sid,'engine.turn',str(i),'started') for i in range(8)]
        stats=postgres_scope.RequestStats();token=postgres_scope.request_stats.set(stats)
        try:assert store.add_engine_timing_batch(entries)==8
        finally:postgres_scope.request_stats.reset(token)
        assert stats.transactions==1 and sum(e[3] for e in stats.trace)==3
        assert [e['message'] for e in store.events(tid)]==list(map(str,range(8)))
    with tenant_context(B):assert store.add_engine_timing_batch(entries)==0
    with tenant_context(A):
        assert len(store.events(tid))==8
        store.delete_task(tid)
        assert store.add_engine_timing_batch(entries)==0


def test_timing_sink_failure_cannot_be_reported_as_success():
    fixture=Path(__file__).parent/'fixtures'/'child_worker_fixture.py'
    def fail(*args):raise RuntimeError('timing storage unavailable')
    s=ChildSupervisor([sys.executable,str(fixture),'timings'],grace_seconds=.1,kill_seconds=.1,timing_sink=fail)
    with pytest.raises(ChildRunError,match='timing'):
        asyncio.run(s.run(decode_operation(frame(operation())),authorize=lambda op:None))
    assert not s.active


def test_one_chunk_is_flushed_live_before_the_child_completes(tmp_path):
    fixture=Path(__file__).parent/'fixtures'/'child_worker_fixture.py'
    ack=tmp_path/'timing-flushed';batches=[]
    def sink(entries):
        batches.append(entries)
        ack.write_text('committed')
    s=ChildSupervisor([sys.executable,str(fixture),'timings-batch',str(ack)],grace_seconds=.1,kill_seconds=.1,timing_batch_sink=sink)
    result=asyncio.run(s.run(decode_operation(frame(operation())),authorize=lambda op:None))
    assert result.returncode==0 and ack.exists()
    assert list(map(len,batches))==[8]


def test_failed_batch_rolls_back_all_events(store,monkeypatch):
    sid='timing-rollback-'+uuid.uuid4().hex
    with tenant_context(A):
        tid=store.create_task('timing-rollback')['task_id'];store.set_engine_session(tid,sid)
        original=store._execute_native
        def after_write(sql,*args,**kwargs):
            result=original(sql,*args,**kwargs)
            if 'jsonb_to_recordset' in sql:raise RuntimeError('test: lost write result')
            return result
        with monkeypatch.context() as m:
            m.setattr(store,'_execute_native',after_write)
            with pytest.raises(RuntimeError,match='lost write result'):
                store.add_engine_timing_batch([(sid,'engine.turn','first','started'),(sid,'engine.turn','second','completed')])
        assert store.events(tid)==[]
        store.delete_task(tid)


def test_batch_sink_failure_is_loud_and_releases_active_slot(tmp_path):
    fixture=Path(__file__).parent/'fixtures'/'child_worker_fixture.py'
    def fail(entries):raise RuntimeError('test: batch failed')
    s=ChildSupervisor([sys.executable,str(fixture),'timings'],grace_seconds=.1,kill_seconds=.1,timing_batch_sink=fail)
    with pytest.raises(ChildRunError,match='timing'):
        asyncio.run(s.run(decode_operation(frame(operation())),authorize=lambda op:None))
    assert not s.active
