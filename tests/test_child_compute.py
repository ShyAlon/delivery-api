"""CPU-second compute charge for phases that run as child processes (child_compute, run_slots watcher, tenant_jobs seam)."""
import asyncio
import sys
import uuid
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from delivery_api import child_compute, config, run_slots
from test_postgres_store import DSN, A
from test_retail_ledger import account  # noqa: F401  (a claimed job with a credit account)
from delivery_api.tenancy import tenant_context
from delivery_api.retail_pricing import RetailRates, UNITS_PER_CREDIT

BURN = "import time\nt=time.process_time()\nwhile time.process_time()-t<%s:pass\n"


def test_cpu_seconds_become_milliseconds_and_unknown_is_zero():
    assert child_compute.cpu_ms(2.5) == Decimal("2500.000")
    assert child_compute.cpu_ms(0.0004) == Decimal("0.400")
    assert child_compute.cpu_ms(None) == Decimal(0)


def test_each_run_gets_its_own_segment_key():
    calls = []
    store = NS(begin_retail_compute=lambda *a: calls.append(a), finish_retail_compute=lambda *a: None)
    first = child_compute.begin(store, "job1", "needs")
    second = child_compute.begin(store, "job1", "needs")
    assert first != second and first.startswith("job1:cpu:")
    assert calls[0][1:3] == ("job1", "needs") and calls[0][3]["formula_version"] == "tokens-plus-compute-v1"
    assert child_compute.available(store) and not child_compute.available(NS())


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="CPU seconds are read from /proc")
def test_watchdog_reads_the_cpu_a_real_child_burns_and_an_idle_child_reads_near_zero(monkeypatch):
    run_slots.reset()
    monkeypatch.setattr(config, "MAX_CONCURRENT_RUNS", 2)
    monkeypatch.setattr(config, "PHASE_TIME_LIMIT_S", 1800)
    store = NS(add_event=lambda *a, **k: None, task=lambda task_id: {})

    async def child(code):
        return await asyncio.create_subprocess_exec(sys.executable, "-c", code, start_new_session=True)

    async def go(task_id, code):
        procs = {}
        orch = NS(runner=NS(processes=procs))
        async def work():
            procs[task_id] = await child(code)
            await procs[task_id].wait()
            await asyncio.sleep(0.05)
            return run_slots.running[task_id].get("cpu_seconds")
        return await run_slots.run_limited(store, orch, task_id, work, tick_s=0.05)

    async def both():
        return await asyncio.gather(go("busy", BURN % 1.0), go("idle", "import time;time.sleep(1.2)"))
    busy, idle = asyncio.run(both())
    assert busy is not None and 0.6 <= busy <= 1.6, busy
    assert idle is not None and idle < 0.3, idle   # sleeping on a model call costs almost no CPU
    run_slots.reset()


pytestmark_pg = pytest.mark.skipif(not DSN, reason="disposable migrated Postgres required")


@pytestmark_pg
def test_cpu_charge_posts_once_at_the_unchanged_price_and_replays_without_a_second_debit(account):  # noqa: F811
    import psycopg
    s, tid, job = account
    with tenant_context(A):
        key = child_compute.begin(s, job, "needs")
        assert child_compute.settle(s, key, 2.5) == Decimal("2500.000")
        assert child_compute.settle(s, key, 2.5) == Decimal("2500.000")   # replay: no second charge
    with psycopg.connect(DSN) as c:
        rows = c.execute("SELECT kind,deterministic_ms,exact_units FROM retail_contributions WHERE job_id=%s", (job,)).fetchall()
        state = c.execute("SELECT state,deterministic_ms FROM retail_compute_segments WHERE segment_key=%s", (key,)).fetchone()
    assert len(rows) == 1 and rows[0][0] == "compute" and rows[0][1] == Decimal("2500")
    expect = RetailRates.from_environment({}).credits(0, Decimal(2500)) * UNITS_PER_CREDIT
    assert Decimal(rows[0][2]) == expect and expect > 0   # 250 ten-millisecond units at PRICE_PER_10MS, nothing else
    assert state[0] == "settled"


@pytestmark_pg
def test_two_runs_of_one_job_are_two_segments_and_a_zero_cpu_run_charges_nothing(account):  # noqa: F811
    import psycopg
    s, tid, job = account
    with tenant_context(A):
        first = child_compute.begin(s, job, "needs");child_compute.settle(s, first, 1.0)
        second = child_compute.begin(s, job, "needs");child_compute.settle(s, second, None)
    with psycopg.connect(DSN) as c:
        n = c.execute("SELECT count(*) FROM retail_compute_segments WHERE job_id=%s AND state='settled'", (job,)).fetchone()[0]
        total = c.execute("SELECT sum(deterministic_ms) FROM retail_contributions WHERE job_id=%s", (job,)).fetchone()[0]
    assert n == 2 and Decimal(total) == Decimal(1000)


@pytestmark_pg
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="CPU seconds are read from /proc")
@pytest.mark.parametrize("fails", [False, True])
def test_a_dispatched_child_run_is_charged_its_cpu_also_when_it_fails(account, monkeypatch, fails):  # noqa: F811
    import psycopg
    from delivery_api import tenant_jobs
    s, tid, _ = account
    run_slots.reset()
    monkeypatch.setattr(config, "CLOUD_TENANTS", True)
    monkeypatch.setattr(config, "RUN_ISOLATION", "process")
    monkeypatch.setattr(config, "MAX_CONCURRENT_RUNS", 2)
    monkeypatch.setattr(config, "PHASE_TIME_LIMIT_S", 1800)
    procs = {}

    async def execute_phase(task_id, phase, **options):
        procs[task_id] = await asyncio.create_subprocess_exec(sys.executable, "-c", BURN % 1.0, start_new_session=True)
        await procs[task_id].wait()
        await asyncio.sleep(0.2)
        if fails:raise RuntimeError("child failed")
    orch = NS(runner=NS(processes=procs), execute_phase=execute_phase)

    async def go():
        with tenant_context(A):
            queued = s.enqueue_worker(tid, "phase:needs")
            try:await tenant_jobs.dispatch_phase(s, orch, tid, "needs", queued_job_id=queued)
            except RuntimeError:
                assert fails
            return queued
    queued = asyncio.run(go())
    with psycopg.connect(DSN) as c:
        seg = c.execute("SELECT state,deterministic_ms FROM retail_compute_segments WHERE job_id=%s", (queued,)).fetchall()
        paid = c.execute("SELECT count(*) FROM retail_contributions WHERE job_id=%s AND kind='compute'", (queued,)).fetchone()[0]
        c.execute("DELETE FROM retail_contributions WHERE job_id=%s", (queued,))
        c.execute("DELETE FROM retail_compute_segments WHERE job_id=%s", (queued,))
    run_slots.reset()
    assert len(seg) == 1 and seg[0][0] == "settled" and paid >= 1
    assert Decimal("650") <= Decimal(seg[0][1]) <= Decimal("1700"), seg
