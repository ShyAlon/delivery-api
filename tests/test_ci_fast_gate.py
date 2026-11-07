"""Every-PR gate stays under five minutes; the heavy matrix is on demand.

Owner decision 2026-11-07: the every-PR gate is the fast unit/contract subset
plus boundary checks, fresh-start acceptance and the control-plane wheel. The
four-shard matrix with browser coverage runs on workflow_dispatch, the nightly
schedule, and ready_for_review only. A normal PR costs ~7 billed minutes
instead of ~31.
"""
from pathlib import Path
import importlib.util
import yaml

ROOT=Path(__file__).resolve().parents[1]
WF=ROOT/'.github'/'workflows'/'ci-cd.yml'

def doc():
    d=yaml.safe_load(WF.read_text())
    assert 'on' in d or True in d
    return d

def on_block(d):
    return d.get('on',d.get(True,{}))

def job(d,name):
    return d['jobs'][name]

def test_gate_job_runs_fast_scope_without_browser_or_matrix():
    d=doc();gate=job(d,'gate')
    assert 'strategy' not in gate,'gate must be a single runner, not the shard matrix'
    steps='\n'.join(str(s.get('run','')) for s in gate['steps'])
    assert 'DELIVERY_TEST_SCOPE=gate' in steps
    assert 'playwright install' not in steps,'gate must not pay the browser download'
    assert 'check-ui-boundaries.py' in steps
    assert 'node --experimental-vm-modules scripts/ci/check-operator-modules.mjs' in steps
    assert 'check-process-control.py' in steps,'gate must enforce the prose-free interview control path'

def test_matrix_job_is_not_part_of_an_ordinary_pr():
    d=doc();matrix=job(d,'matrix')
    cond=str(matrix.get('if',''))
    assert 'workflow_dispatch' in cond and 'schedule' in cond and 'ready_for_review' in cond,cond
    assert 'pull_request' not in cond.replace('ready_for_review',''),cond

def test_weekly_schedule_keeps_full_depth():
    sched=(on_block(doc()) or {}).get('schedule')
    assert sched,'weekly full-matrix run missing'
    crons=[e['cron'] for e in sched if 'cron' in e]
    assert len(crons)==1,crons
    fields=crons[0].split()
    assert len(fields)==5 and fields[4]!='*',(
        'owner decision 2026-11-07: full matrix weekly, not nightly; '
        f'cron must pin a day-of-week, got {crons[0]!r}')

def test_image_still_waits_on_the_full_matrix():
    d=doc();image=job(d,'image')
    assert 'matrix' in image['needs'],image['needs']

def test_classifier_partitions_every_test_file():
    spec=importlib.util.spec_from_file_location('classify_tests',ROOT/'scripts'/'ci'/'classify_tests.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    fast,heavy=m.classify()
    all_files=sorted(p.relative_to(ROOT).as_posix() for p in (ROOT/'tests').rglob('test_*.py'))
    assert sorted(fast+heavy)==all_files,'classification must partition every test file'
    assert not set(fast)&set(heavy)
    for known_heavy in ('tests/test_ui_playwright.py','tests/test_postgres_store.py',
                        'tests/test_local_pg_preview.py','tests/test_answer_watch_budget.py'):
        assert known_heavy in heavy,known_heavy
    # This file itself is heavy on purpose: it carries the heavy tokens as data.
    for known_fast in ('tests/test_ci_trigger_scope.py','tests/test_stop_run.py'):
        assert known_fast in fast,known_fast
    assert 'tests/test_ci_fast_gate.py' in heavy
    assert m.classify()==(fast,heavy),'classification must be deterministic'
