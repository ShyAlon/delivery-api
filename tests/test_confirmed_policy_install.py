"""Operator installation preview/apply/rollback in disposable PG, no live changes."""
import pytest
from delivery_api.confirmed_policy_install import plan
from delivery_api.attempt_ledger import AdmissionDenied
from delivery_api.tenancy import tenant_context
from test_attempt_ledger import credit_account,A,DSN


from policy_database import isolated_policy_database

@pytest.fixture
def quiet(monkeypatch,isolated_policy_database):
    from delivery_api.postgres_store import PostgresStore
    from psycopg.conninfo import make_conninfo
    import uuid
    monkeypatch.setitem(globals(),'DSN',isolated_policy_database)
    store=PostgresStore(make_conninfo(isolated_policy_database,user='delivery_app'))
    with tenant_context(A):
        tid=store.create_task('Policy isolated')['task_id']
        store.set_engine_session(tid,'policy-'+uuid.uuid4().hex)
        job=store.enqueue_worker(tid,'fake');store.claim_worker(job);store.finish_worker(job)
    return store,job


def test_preview_commit_replay_and_rollback_only_change_policy(quiet):
    import psycopg
    with psycopg.connect(DSN) as c:
        before=c.execute('SELECT * FROM admission_limits').fetchall()
        assert not plan(c)['already_installed']
        assert c.execute('SELECT * FROM expense_guard_policy').fetchall()==[]
        assert plan(c,apply=True)['activation_enabled'] is False
        assert plan(c,apply=True)['already_installed']
        assert c.execute('SELECT * FROM admission_limits').fetchall()==before
        assert c.execute('SELECT * FROM own_test_expense_profiles').fetchall()==[]
        c.rollback()
    with psycopg.connect(DSN) as c:assert c.execute('SELECT * FROM expense_guard_policy').fetchall()==[]


@pytest.mark.parametrize('sql,reason',[
    ("UPDATE worker_jobs SET state='running'",'quiescent'),
    ("INSERT INTO expense_guard_policy VALUES(true,'different',1,1,1,1)",'differs'),
])
def test_install_refuses_existing_conflict_or_live_work(quiet,sql,reason):
    import psycopg
    with psycopg.connect(DSN) as c:
        c.execute(sql)
        with pytest.raises(AdmissionDenied,match=reason):plan(c,apply=True)
        c.rollback()


def test_script_requires_both_operator_acknowledgments_and_never_launches():
    from pathlib import Path
    text=(Path(__file__).parents[1]/'scripts/ops/confirmed-expense-policy.sh').read_text()
    assert "ack!='reviewed runtime includes production launch guard'" in text and "ack!='yes'" in text
    assert text.index('plan(conn),')<text.index('plan(conn,apply=True)')
    for unsafe in ('docker run','start-next-phase','DELETE FROM','UPDATE admission_limits','API_KEY'):assert unsafe not in text


def test_hidden_accounting_is_not_quiescent(quiet):
    import psycopg
    with psycopg.connect(DSN.replace('user=sandbox','user=delivery_app')) as c,pytest.raises(AdmissionDenied,match='inspection unavailable'):plan(c,apply=True)


def test_blocker_details_name_jobs_and_keep_output_bounded(quiet):
    import json,psycopg
    _,job=quiet
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE worker_jobs SET state='running' WHERE job_id=%s",(job,))
        with pytest.raises(AdmissionDenied) as exc:plan(c,apply=True)
        blockers=json.loads(str(exc.value).split(': ',1)[1])
        match=next(b for b in blockers if b['table']=='worker_jobs')
        assert job in [r['id'] for r in match['records']]
        assert all(set(r)=={'id','owner_id','state'} for r in match['records'])
        assert len(match['records'])<=20 and match['count']>=1
        assert c.execute('SELECT * FROM expense_guard_policy').fetchall()==[]
        c.rollback()


def test_installer_renders_policy_denial_without_traceback():
    import ast
    from pathlib import Path
    text=(Path(__file__).parents[1]/'scripts/ops/confirmed-expense-policy.sh').read_text()
    code=text.split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0]
    tree=ast.parse(code)
    handler=next(n for n in ast.walk(tree) if isinstance(n,ast.ExceptHandler) and getattr(n.type,'id','')=='AdmissionDenied')
    error=next(n for n in ast.walk(handler) if isinstance(n,ast.Raise))
    assert isinstance(error.cause,ast.Constant) and error.cause.value is None
    assert 'POLICY BLOCKED:' in code and 'No policy change committed.' in code


def test_blocker_details_count_all_jobs_but_print_only_twenty(quiet):
    import json,psycopg
    _,job=quiet
    with psycopg.connect(DSN) as c:
        c.execute("INSERT INTO worker_jobs(job_id,owner_id,auth_session_id,task_id,session_id,generation,kind,state,attempt,created_at,updated_at) SELECT job_id||'-block-'||i,owner_id,auth_session_id,task_id,session_id,generation,kind,'pending',attempt,created_at,updated_at FROM worker_jobs CROSS JOIN generate_series(1,25) i WHERE job_id=%s",(job,))
        with pytest.raises(AdmissionDenied) as exc:plan(c)
        match=next(b for b in json.loads(str(exc.value).split(': ',1)[1]) if b['table']=='worker_jobs')
        assert match['count']>=25 and len(match['records'])==20 and match['truncated']
        c.rollback()


def test_dry_plan_does_not_wait_on_policy_row_and_diagnostics_name_holder(quiet):
    import psycopg
    from delivery_api.db_lock_diagnostics import inspect
    with psycopg.connect(DSN) as holder,psycopg.connect(DSN) as reader:
        holder.execute('SELECT pg_advisory_xact_lock(73194720315621::bigint)')
        pid=holder.execute('SELECT pg_backend_pid()').fetchone()[0]
        reader.execute("SET LOCAL statement_timeout='200ms'")
        plan(reader)
        assert any(r['pid']==pid and any(l['type']=='advisory' for l in r['locks']) for r in inspect(reader)['sessions'])
        assert not any(r['pid']==reader.execute('SELECT pg_backend_pid()').fetchone()[0] for r in inspect(reader)['sessions'])
        holder.rollback();reader.rollback()


def test_preview_transaction_is_closed_before_confirmation_prompts():
    from pathlib import Path
    text=(Path(__file__).parents[1]/'scripts/ops/confirmed-expense-policy.sh').read_text()
    assert text.index('conn.rollback()')<text.index("print('Type exactly:")
    assert 'pg_terminate_backend' not in text and 'pg_cancel_backend' not in text
