"""Session-cost tooltip: credits per phase, customer-safe, wired into the header."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]/'src'/'delivery_api'


@pytest.fixture
def phase_cost_workspace(pg_workspace_factory):
    from delivery_api.tenancy import tenant_context
    from delivery_api.retail_pricing import RetailRates,UNITS_PER_CREDIT
    from uuid import uuid4
    with pg_workspace_factory() as workspace,tenant_context(workspace.scope):
        store=workspace.store
        with workspace.admin() as connection:
            connection.execute('INSERT INTO credit_accounts(owner_id,balance_units,units_per_credit,updated_at) VALUES(%s,%s,%s,0)',(workspace.scope.owner_id,100*UNITS_PER_CREDIT,UNITS_PER_CREDIT))
        def seed(title,rows):
            tid=store.create_task(title)['task_id']
            store.set_engine_session(tid,'phase-cost-'+uuid4().hex)
            job=store.enqueue_worker(tid,'phase:needs')
            rates=RetailRates('.5','0','1').snapshot()
            for phase,credits in rows:
                store.post_retail_contribution(key=job+':'+uuid4().hex,job_id=job,phase=phase,kind='tokens',input_tokens=int(credits*2),rates=rates)
            return tid
        yield workspace,seed


def test_phase_totals_sum_each_phase_and_sort_largest_first(phase_cost_workspace):
    workspace,seed=phase_cost_workspace
    # One test token is half a credit; production exact posting computes the rows.
    tid=seed('Phase totals',[('intent',.5),('spec',3),('intent',.5),('conops',1)])
    out=workspace.store.task_phase_credits(tid)
    assert out==[{'phase':'spec','credits':3.0},{'phase':'conops','credits':1.0},{'phase':'intent','credits':1.0}]
    assert sum(p['credits'] for p in out)==5.0


def test_phase_totals_read_only_this_task_and_return_credits_only(phase_cost_workspace,pg_workspace_factory):
    from delivery_api.tenancy import tenant_context
    workspace,seed=phase_cost_workspace
    tid=seed('Selected task',[('spec',1)])
    seed('Other task',[('intent',9)])
    with workspace.admin() as connection:
        before=connection.execute('SELECT count(*),sum(exact_units) FROM retail_contributions WHERE owner_id=%s',(workspace.scope.owner_id,)).fetchone()
        balance=connection.execute('SELECT balance_units,fractional_debit_units FROM credit_accounts WHERE owner_id=%s',(workspace.scope.owner_id,)).fetchone()
    out=workspace.store.task_phase_credits(tid)
    assert out==[{'phase':'spec','credits':1.0}]
    assert set(out[0])=={'phase','credits'}
    assert all('rates' not in row and 'usd' not in row for row in out)
    with workspace.admin() as connection:
        assert connection.execute('SELECT count(*),sum(exact_units) FROM retail_contributions WHERE owner_id=%s',(workspace.scope.owner_id,)).fetchone()==before
        assert connection.execute('SELECT balance_units,fractional_debit_units FROM credit_accounts WHERE owner_id=%s',(workspace.scope.owner_id,)).fetchone()==balance
    with pg_workspace_factory() as other,tenant_context(other.scope):
        assert other.store.task_phase_credits(tid)==[]


def test_phase_totals_empty_when_nothing_is_posted(phase_cost_workspace):
    workspace,seed=phase_cost_workspace
    assert workspace.store.task_phase_credits(seed('No posted phases',[]))==[]


def test_route_and_store_are_wired_and_customer_safe():
    app=(ROOT/'app.py').read_text();store=(ROOT/'postgres_store.py').read_text();ledger=(ROOT/'retail_ledger.py').read_text()
    assert '@app.get("/api/tasks/{task_id}/cost-phases")' in app and "store.task_phase_credits(task_id)" in app
    assert 'def task_phase_credits(self,task_id)' in store
    fn=ledger[ledger.index('def task_phase_totals'):ledger.index('def _job_evidence')]
    assert 'usd' not in fn.lower() and 'rates' not in fn


def run_node(code):
    node=shutil.which('node')
    if not node:pytest.skip('node is not installed')
    tmp=Path(__file__).parent/'_cost_breakdown_probe.mjs'
    tmp.write_text((ROOT/'web'/'cost-breakdown.js').read_text()+'\n'+code)
    try:return subprocess.run([node,str(tmp)],capture_output=True,text=True,timeout=30,check=True).stdout
    finally:tmp.unlink()


def test_tooltip_lists_phases_and_total():
    out=json.loads(run_node("console.log(JSON.stringify([costTitle(1.97,[{phase:'spec',credits:1.5},{phase:'intent',credits:0.47}]),costTitle(1,[]),costTitle(1,null),costTitle(1,[{phase:'x',credits:'a'}]),phaseLabel('product_definition'),phaseLabel('')]))"))
    assert out[0]=='Rolling Session Cost\nSpec: 1.50 credits\nIntent: 0.47 credits\nTotal: 1.97 credits'
    assert out[1]==out[2]==out[3]=='Rolling Session Cost'
    assert out[4]=='Product definition' and out[5]=='Other'


def test_header_asks_for_the_breakdown_only_for_a_task_and_keeps_the_total_on_failure():
    js=(ROOT/'web'/'session-cost.js').read_text()
    assert "import {costTitle} from './cost-breakdown.js'" in js
    assert "if(task){try{const parts=await api(`${route}-phases`)" in js and 'catch{/* the total stands' in js
    assert "host.title='Rolling Session Cost';" in js


def test_tooltip_text_has_no_money_or_vendor_words():
    js=(ROOT/'web'/'cost-breakdown.js').read_text().lower()
    for word in ('usd','vendor','expense','spend'):assert word not in js
    assert '$'+'{' in js and '$'+'(' not in js and 'price_usd' not in js
