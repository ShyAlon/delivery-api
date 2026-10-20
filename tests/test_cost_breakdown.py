"""Session-cost tooltip: credits per phase, customer-safe, wired into the header."""
import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]/'src'/'delivery_api'


class Db:
    def __init__(self,rows):self.rows,self.asked=rows,[]
    def execute(self,sql,params):
        self.asked.append((sql,params))
        class R:
            def fetchall(s):return self.rows
        return R()


class Store:
    def __init__(self,rows):self._db=Db(rows)


def test_phase_totals_sum_each_phase_and_sort_largest_first():
    rl=pytest.importorskip('delivery_api.retail_ledger')
    per=rl.UNITS_PER_CREDIT
    rows=[{'phase_key':'intent','exact_units':per//2},{'phase_key':'spec','exact_units':3*per},{'phase_key':'intent','exact_units':per//2},{'phase_key':'conops','exact_units':per}]
    out=rl.task_phase_totals(Store(rows),'t1')
    assert out==[{'phase':'spec','credits':3.0},{'phase':'conops','credits':1.0},{'phase':'intent','credits':1.0}]
    assert sum(p['credits'] for p in out)==5.0


def test_phase_totals_read_only_this_task_and_return_credits_only():
    rl=pytest.importorskip('delivery_api.retail_ledger')
    store=Store([{'phase_key':'spec','exact_units':rl.UNITS_PER_CREDIT}])
    out=rl.task_phase_totals(store,'t1')
    sql,params=store._db.asked[0]
    assert params==('t1',) and sql.strip().upper().startswith('SELECT') and 'task_id=?' in sql
    assert set(out[0])=={'phase','credits'}
    assert 'rates' not in sql and 'usd' not in sql.lower()


def test_phase_totals_empty_when_nothing_is_posted():
    rl=pytest.importorskip('delivery_api.retail_ledger')
    assert rl.task_phase_totals(Store([]),'t1')==[]


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
