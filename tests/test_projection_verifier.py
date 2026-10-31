import importlib.util
from pathlib import Path
from test_postgres_store import store,A,DSN
from delivery_api.tenancy import tenant_context
import psycopg

def module():
    p=Path(__file__).parents[1]/'scripts/owner/check-projection-totals.py';s=importlib.util.spec_from_file_location('projection_check',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def test_readonly_queries_and_lazy_state_do_not_initialize(store):
    m=module()
    with psycopg.connect(DSN,autocommit=True) as c:
        c.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        before=c.execute('SELECT count(*) FROM expense_total_clock').fetchone()[0]
        result,code=m.check(c)
        assert result['retail']['mismatches']==0
        assert result['admission']['mismatches']==0
        assert result['expense']['mismatches'] in (None,0)
        assert c.execute('SELECT count(*) FROM expense_total_clock').fetchone()[0]==before
        c.execute('ROLLBACK')

def test_expense_formula_detects_tampered_projection(store):
    m=module()
    with psycopg.connect(DSN) as c:
        c.execute('SELECT requi_refresh_expense_totals(1000000)')
        assert c.execute(m.EXPENSE).fetchone()[0]==0
        c.execute("UPDATE expense_totals SET value=value+1 WHERE bucket='users'")
        assert c.execute(m.EXPENSE).fetchone()[0]==1
        c.rollback()

from test_attempt_ledger import ledger,Q
from test_retail_ledger import account

def test_admission_formula_matches_real_attempt_and_detects_drift(ledger):
    import uuid,time
    s,task,job=ledger;m=module()
    with tenant_context(A):
        s.reserve_provider_attempt(job,uuid.uuid4().hex,Q,'check')
        with s._task_operation_guard('task.read'):
            s._execute_native('SELECT * FROM requi_admission_totals(%s)',(time.time(),)).fetchone()
    with psycopg.connect(DSN) as c:
        assert c.execute(m.ADMISSION).fetchone()[0]==0
        c.execute('UPDATE admission_totals SET held=held+1 WHERE owner_id=%s',(A.owner_id,))
        assert c.execute(m.ADMISSION).fetchone()[0]==1
        c.rollback()


def test_retail_formula_matches_frozen_money_and_detects_drift(account):
    import uuid
    from delivery_api.retail_pricing import RetailRates
    s,task,job=account;m=module()
    with tenant_context(A):s.post_retail_contribution(key=uuid.uuid4().hex,job_id=job,phase='intent',kind='tokens',input_tokens=3,rates=RetailRates('0.0000125','0.0000005','2').snapshot())
    with psycopg.connect(DSN) as c:
        assert c.execute(m.RETAIL).fetchone()[0]==0
        c.execute("UPDATE retail_totals SET credits=credits+1 WHERE scope='task' AND ref=%s",(task,))
        assert c.execute(m.RETAIL).fetchone()[0]==1
        c.rollback()
