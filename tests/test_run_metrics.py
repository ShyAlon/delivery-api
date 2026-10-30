"""Operator resource samples: what is read from /proc, what is stored, how raw rows become summaries, and that a broken store never stops a run."""
import asyncio
import os
import sys
import time
from types import SimpleNamespace as NS

import pytest

from delivery_api import config, run_metrics, run_slots
from pg_harness import STATE
import psycopg

DAY = 86400


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    with psycopg.connect(STATE['dsn'],autocommit=True) as c:
        c.execute('TRUNCATE resource_run_samples,resource_run_summaries,resource_container_samples,resource_container_summaries')
    monkeypatch.setenv('DELIVERY_METRICS_BACKEND','postgres')
    monkeypatch.setenv('DELIVERY_METRICS_SOURCE','ported-metrics-tests')
    monkeypatch.setattr(config,'DATABASE_URL',STATE['dsn'].replace('user=sandbox','user=delivery_app'))
    monkeypatch.setattr(run_metrics, "CLK_TCK", 100)
    monkeypatch.setattr(run_metrics, "PAGE_BYTES", 4096)
    run_metrics.reset(); run_slots.reset()
    yield
    run_metrics.reset(); run_slots.reset()


def _proc(root, pid, pgrp, utime=0, stime=0, cutime=0, cstime=0, rss_pages=0, comm="python"):
    folder = root / str(pid)
    folder.mkdir()
    after = ["S", "1", str(pgrp), "0", "0", "0", "0", "0", "0", "0", "0", str(utime), str(stime), str(cutime), str(cstime), "20", "0", "1", "0", "1", "1000", str(rss_pages)]
    (folder / "stat").write_text("%d (%s) %s\n" % (pid, comm, " ".join(after)))


def test_a_group_is_summed_and_only_its_members_count(tmp_path, monkeypatch):
    _proc(tmp_path, 100, 100, utime=300, stime=100, cutime=50, cstime=50, rss_pages=25600, comm="python (gen) x")
    _proc(tmp_path, 101, 100, utime=200, stime=0, rss_pages=12800)
    _proc(tmp_path, 200, 200, utime=9999, rss_pages=99999)
    (tmp_path / "self").mkdir()
    monkeypatch.setattr(run_metrics, "PROC_DIR", str(tmp_path))
    got = run_metrics.read_group(100)
    assert got == {"rss_mb": 150.0, "cpu_seconds": 7.0, "procs": 2}


def test_a_process_that_is_gone_or_a_missing_proc_reads_as_none(tmp_path, monkeypatch):
    monkeypatch.setattr(run_metrics, "PROC_DIR", str(tmp_path))
    assert run_metrics.read_group(555) is None and run_metrics.read_group("nope") is None
    monkeypatch.setattr(run_metrics, "PROC_DIR", str(tmp_path / "missing"))
    assert run_metrics.read_group(100) is None


def _group(monkeypatch, rows):
    seq = iter(rows)
    monkeypatch.setattr(run_metrics, "read_group", lambda pid: next(seq))


def test_cpu_percent_comes_from_the_change_since_the_last_sample_and_peak_only_grows(monkeypatch):
    sampler = run_metrics.RunSampler("t1", "sess1", "needs", 1000.0)
    _group(monkeypatch, [{"rss_mb": 100.0, "cpu_seconds": 5.0, "procs": 1}, {"rss_mb": 300.0, "cpu_seconds": 15.0, "procs": 2}, {"rss_mb": 200.0, "cpu_seconds": 15.0, "procs": 1}])
    a = sampler.sample(42, now=1010.0); b = sampler.sample(42, now=1020.0); c = sampler.sample(42, now=1030.0)
    assert (a["cpu_pct"], b["cpu_pct"], c["cpu_pct"]) == (50.0, 100.0, 0.0)
    assert (a["peak_rss_mb"], b["peak_rss_mb"], c["peak_rss_mb"]) == (100.0, 300.0, 300.0)


def test_every_raw_row_carries_task_phase_and_session(monkeypatch):
    sampler = run_metrics.RunSampler("t1", "sess1", "needs", time.time() - 10)
    _group(monkeypatch, [{"rss_mb": 100.0, "cpu_seconds": 1.0, "procs": 1}])
    sampler.sample(42)
    got = run_metrics.for_task("t1")["samples"]
    assert len(got) == 1 and got[0]["session_id"] == "sess1" and got[0]["phase"] == "needs" and got[0]["run_key"] == sampler.run_key


def _put(run_key, ts, rss, pct, cpu, task="t1", session="sess1", phase="needs"):
    run_metrics._pg().append_run(ts=ts,run_key=run_key,task_id=task,session_id=session,phase=phase,pid=1,procs=1,rss_mb=rss,peak_rss_mb=rss,cpu_seconds=cpu,cpu_pct=pct)


def test_stale_raw_rows_become_one_summary_with_average_and_maximum_and_are_deleted():
    now = 100 * DAY
    for i, (rss, pct) in enumerate([(100, 20), (300, 80), (200, 50)]):
        _put("old", now - 5 * DAY + i, rss, pct, i + 1)
    runs, days = run_metrics.rollup(now, raw_days=3)
    got = run_metrics.for_task("t1")
    assert (runs, days) == (1, 0) and got["samples"] == []
    s = got["summaries"][0]
    assert (s["avg_rss_mb"], s["max_rss_mb"], s["avg_cpu_pct"], s["max_cpu_pct"], s["samples"], s["cpu_seconds"]) == (200.0, 300.0, 50.0, 80.0, 3, 3)
    assert (s["task_id"], s["session_id"], s["phase"]) == ("t1", "sess1", "needs")


def test_recent_rows_and_a_run_that_straddles_the_cutoff_stay_raw_and_rolling_twice_changes_nothing():
    now = 100 * DAY
    _put("new", now - DAY, 100, 10, 1)
    _put("mixed", now - 3 * DAY - 5, 100, 10, 1); _put("mixed", now - 3 * DAY + 5, 100, 10, 2)
    assert run_metrics.rollup(now, raw_days=3) == (0, 0)
    assert len(run_metrics.for_task("t1")["samples"]) == 3
    _put("old", now - 9 * DAY, 100, 10, 1)
    assert run_metrics.rollup(now, raw_days=3) == (1, 0) and run_metrics.rollup(now, raw_days=3) == (0, 0)
    assert len(run_metrics.for_task("t1")["summaries"]) == 1 and len(run_metrics.for_task("t1")["samples"]) == 3


def test_container_samples_roll_up_by_whole_stale_day():
    pg=run_metrics._pg()
    old_day=90*DAY
    for i,(mem,pct) in enumerate([(1000,10.0),(2000,30.0)]):
        pg.append_container(ts=old_day+i*60,mem_used_mb=mem,mem_limit_mb=3072,cpu_seconds=1.0,cpu_pct=pct)
    pg.append_container(ts=99*DAY,mem_used_mb=500,mem_limit_mb=3072,cpu_seconds=1.0,cpu_pct=5.0)
    assert run_metrics.rollup(100*DAY,raw_days=3)==(0,1)
    with psycopg.connect(STATE['dsn'],row_factory=psycopg.rows.dict_row) as conn:
        assert conn.execute('SELECT count(*) AS n FROM resource_container_samples').fetchone()['n']==1
        row=conn.execute('SELECT * FROM resource_container_summaries').fetchone()
    assert (row['samples'],row['avg_mem_mb'],row['max_mem_mb'],row['avg_cpu_pct'],row['max_cpu_pct'])==(2,1500.0,2000.0,20.0,30.0)


def test_the_overview_text_shows_runs_the_container_and_a_timeline(monkeypatch):
    now = time.time()-10*DAY
    for i, rss in enumerate([100, 250, 400]):
        _put("live", now - 30 + i * 10, rss, 40 + i, i)
    _put("kept", now - 2 * DAY, 150, 10, 1, task="t2", phase="design")
    run_metrics.rollup(now + 5 * DAY, raw_days=3)
    _put("live2", now - 5, 321, 12, 1, task="t3")
    run_metrics._pg().append_container(ts=now,mem_used_mb=1200,mem_limit_mb=3072,cpu_seconds=5.0,cpu_pct=75.0)
    text = run_metrics.metrics_text(run_metrics.overview())
    assert "memory 1200 of 3072 MB" in text and "CPU 75% of a core" in text
    assert "memory MB over time: 321" in text and "(summary)" in text and "session sess1" in text


def test_nothing_sampled_yet_is_said_plainly_and_creates_no_file(tmp_path):
    text = run_metrics.metrics_text(run_metrics.overview())
    assert "No run has been sampled yet" in text and not (tmp_path / "metrics.db").exists()


def test_a_store_that_cannot_be_written_is_named_and_does_not_raise(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from delivery_api.postgres_metrics import PostgresMetrics
    @contextmanager
    def unavailable(self):
        raise ConnectionError('fixture unavailable')
        yield
    monkeypatch.setattr(PostgresMetrics,'transaction',unavailable)
    run_metrics.reset()
    info = {"started": time.time()}
    _group(monkeypatch, [{"rss_mb": 10.0, "cpu_seconds": 1.0, "procs": 1}])
    run_metrics.tick(info, "t1", 42, None)
    text = run_metrics.metrics_text(run_metrics.overview())
    assert "NOT being stored" in text


def test_no_money_or_vendor_wording_in_the_metrics_text():
    _put("a", time.time(), 100, 10, 1)
    ov = run_metrics.overview()
    text = run_metrics.metrics_text(ov).replace(ov["path"], "FILE").lower()   # the temp path holds the test name
    for word in ("expense", "vendor", "cost", "price", "$", "usd", "spend", "credit"):
        assert word not in text


@pytest.mark.skipif(not os.path.isdir("/proc/self"), reason="needs /proc")
def test_a_real_run_is_sampled_with_its_session_and_phase(monkeypatch):
    monkeypatch.setattr(config, "METRICS_INTERVAL_S", 2)
    events = []
    store = NS(add_event=lambda *a, **k: events.append((a, k)), task=lambda task_id: {"active_phase": "needs", "engine_session_id": "sess9"})
    procs = {}
    async def go():
        child = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; t=time.time()\nwhile time.time()-t<0.6: pass", start_new_session=True)
        procs["t1"] = child
        async def work():
            await child.wait(); return True
        await run_slots.run_limited(store, NS(runner=NS(processes=procs)), "t1", work, tick_s=0.05)
    asyncio.run(go())
    got = run_metrics.for_task("t1")["samples"]
    assert got and got[0]["session_id"] == "sess9" and got[0]["phase"] == "needs" and got[0]["rss_mb"] > 0


def test_the_capacity_snapshot_text_includes_the_resource_section():
    snap = {"isolation": "process", "max_concurrent_runs": 2, "time_limit_s": 1800, "running": [], "waiting": 0, "recent_peaks": [],
            "host": {"cpus": 2, "mem_total_mb": 3072, "mem_available_mb": 2000},
            "metrics": {"path": "/x/run_metrics.db", "problem": None, "runs": [], "container": None, "raw_days": 3, "interval_s": 10}}
    assert "Resource samples: every 10 s" in run_slots.snapshot_text(snap)
