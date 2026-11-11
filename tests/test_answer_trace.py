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
