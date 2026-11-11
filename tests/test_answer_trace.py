import pytest
from delivery_api.answer_trace import WireTrace,tables
from test_postgres_store import DSN


def test_wire_trace_counts_real_query_and_pipeline_barriers_without_sql():
    import psycopg
    trace=WireTrace();trace.install()
    try:
        with psycopg.connect(DSN,autocommit=True) as c:
            trace.reset()
            c.execute('SELECT 123456789 AS private_value').fetchone()
            with c.pipeline():
                c.execute('SELECT 42');c.execute('SELECT 43')
            rows=trace.snapshot()
            assert sum(row['syncs'] for row in rows)==2,rows
            assert not trace.failed
            assert '123456789' not in repr(rows) and 'SELECT' not in repr(rows)
    finally:trace.restore()


def test_parent_tables_count_syncs_not_trace_entries():
    result=tables([('child_interview:run','BEGIN',0,0),('child_interview:run','query',2,1),('stop_watch:watch_for_stop','read',3,1)])
    assert result['syncs']==2
    assert sum(r['syncs'] for r in result['phases'])==2
    assert sum(r['duration_ms'] for r in result['callers'])==5


def test_trace_failure_does_not_break_database_work(monkeypatch):
    import os,psycopg
    def unavailable(*a,**kw):raise OSError('anonymous fd unavailable')
    monkeypatch.setattr(os,'memfd_create',unavailable)
    trace=WireTrace();trace.install()
    try:
        with psycopg.connect(DSN,autocommit=True) as c:assert c.execute('SELECT 7').fetchone()==(7,)
        assert trace.failed
    finally:trace.restore()


def test_engine_identity_setup_has_one_barrier_not_two():
    import psycopg
    from interview_engine.pg_claims import apply_claims
    trace=WireTrace();trace.install()
    try:
        with psycopg.connect(DSN,autocommit=True) as c:
            c.execute('BEGIN');trace.reset()
            apply_claims(c,('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-0000000000a1'))
            assert sum(row['syncs'] for row in trace.snapshot())==1
            assert c.execute("SELECT current_user,current_setting('request.jwt.claim.sub',true)").fetchone()==('requi_backend','00000000-0000-0000-0000-000000000001')
            c.execute('ROLLBACK')
            assert c.execute('SELECT current_user').fetchone()[0]=='sandbox'
    finally:trace.restore()


def test_scoped_transaction_has_no_implicit_duplicate_begin(pg_workspace_factory):
    from delivery_api.tenancy import tenant_context
    with pg_workspace_factory() as w,tenant_context(w.scope):
        trace=WireTrace();trace.install()
        try:
            # Warm the pool so connection liveness is a separate measured cost.
            w.store.task('absent')
            trace.reset()
            with w.store._postgres.transaction() as conn:
                assert conn.execute('SELECT 7 AS value').fetchone()['value']==7
            assert sum(row['syncs'] for row in trace.snapshot())==3,trace.snapshot()
            with w.store._postgres._pool.connection() as raw:assert raw.autocommit is False
        finally:trace.restore()


def test_concurrent_context_traces_are_isolated_and_restore_out_of_order():
    import threading,psycopg
    from concurrent.futures import ThreadPoolExecutor
    original=psycopg.Connection.wait
    installed=threading.Barrier(2);first_done=threading.Event();second_done=threading.Event()
    def work(first):
        trace=WireTrace();trace.install(context_only=True)
        try:
            installed.wait(5)
            with psycopg.connect(DSN,autocommit=True) as c:
                trace.reset()
                if not first:assert first_done.wait(5)
                c.execute('SELECT 1').fetchone()
            return trace.snapshot()
        finally:
            trace.restore()
            if first:first_done.set();assert second_done.wait(5)
            else:second_done.set()
    with ThreadPoolExecutor(2) as pool:
        one=pool.submit(work,True);two=pool.submit(work,False)
        assert sum(r['syncs'] for r in one.result())==1
        assert sum(r['syncs'] for r in two.result())==1
    assert psycopg.Connection.wait is original
