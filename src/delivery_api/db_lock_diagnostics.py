"""Read-only current sessions/locks, never query text, DSNs or backend control."""
import json


def inspect(conn):
    rows=conn.execute("""SELECT a.pid,a.state,a.application_name,a.wait_event_type,a.wait_event,
      extract(epoch FROM clock_timestamp()-a.query_start),
      extract(epoch FROM clock_timestamp()-a.xact_start),pg_blocking_pids(a.pid),
      COALESCE((SELECT jsonb_agg(DISTINCT jsonb_build_object('type',l.locktype,
        'mode',l.mode,'granted',l.granted,'relation',c.relname)) FROM pg_locks l
        LEFT JOIN pg_class c ON c.oid=l.relation WHERE l.pid=a.pid),'[]'::jsonb)
      FROM pg_stat_activity a WHERE a.datname=current_database() AND a.pid<>pg_backend_pid()
      ORDER BY a.xact_start NULLS LAST,a.pid LIMIT 100""").fetchall()
    return {'sessions':[dict(zip(('pid','state','application_name','wait_event_type','wait_event',
        'query_age_seconds','transaction_age_seconds','blocked_by','locks'),r)) for r in rows],
        'limit':100,'note':'Current sessions, not proof of an expired wait edge. Restricted roles may hide fields. No query text included. No sessions cancelled or terminated.'}


def report(dsn):
    import psycopg
    try:
        with psycopg.connect(dsn,connect_timeout=5,options='-c default_transaction_read_only=on -c statement_timeout=3000',application_name='requi-lock-inspection') as conn:
            print(json.dumps(inspect(conn),indent=2,default=str))
    except Exception:
        print('Read-only session inspection unavailable; no automatic retry, cancellation or termination.')
