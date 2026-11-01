"""Hermetic database sessions, no live credentials or backend control."""
import psycopg
from delivery_api.db_lock_diagnostics import inspect,report
from test_postgres_store import DSN


def test_inspection_names_other_idle_transaction_and_excludes_self(store,capsys):
    with psycopg.connect(DSN,application_name='requi-test-holder') as holder,psycopg.connect(DSN,options='-c default_transaction_read_only=on') as reader:
        pid=holder.execute('SELECT pg_backend_pid()').fetchone()[0]
        holder.execute('SELECT * FROM public.tasks LIMIT 1')
        self_pid=reader.execute('SELECT pg_backend_pid()').fetchone()[0]
        result=inspect(reader)
        session=next(s for s in result['sessions'] if s['pid']==pid)
        assert session['application_name']=='requi-test-holder' and session['state']=='idle in transaction'
        assert session['transaction_age_seconds']>=0 and session['locks']
        assert all(s['pid']!=self_pid for s in result['sessions'])
        assert all('query' not in s for s in result['sessions'])
        report(DSN)
        assert 'requi-test-holder' in capsys.readouterr().out


def test_reset_timeout_is_caught_for_inventory_as_well_as_apply():
    import ast
    from pathlib import Path
    text=(Path(__file__).parents[1]/'scripts/owner/reset-preview.py').read_text()
    tree=ast.parse(text)
    handler=next(n for n in ast.walk(tree) if isinstance(n,ast.ExceptHandler) and isinstance(n.type,ast.Tuple))
    assert 'LockNotAvailable' in ast.unparse(handler.type) and 'QueryCanceled' in ast.unparse(handler.type)
    assert 'report(diagnostic_dsn)' in ast.unparse(handler)
