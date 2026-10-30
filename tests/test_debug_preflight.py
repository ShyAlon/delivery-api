"""The Debug dialog's Startup Checks section reads back what the container
preflight wrote at startup (the 2026-10-26 stale-mount incident: the answer to
'why did my task fail' must be one click, not a ZIP export and a traceback)."""
import json

from delivery_api import preflight_read


def test_the_startup_report_is_read_back(tmp_path, monkeypatch,pg_log_evidence):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    target = tmp_path / 'requi-re' / 'logs' / 'preflight-report.json'
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({'status':'warn','checks':[{'name':'pool-budget'}]}))
    assert preflight_read.startup_report()['status'] == 'warn'


def test_a_missing_or_corrupt_report_is_none_not_an_error(tmp_path, monkeypatch,pg_log_evidence):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    assert preflight_read.startup_report() is None
    target = tmp_path / 'requi-re' / 'logs' / 'preflight-report.json'
    target.parent.mkdir(parents=True)
    target.write_text('{corrupt')
    assert preflight_read.startup_report() is None


def test_the_endpoint_returns_the_startup_section(tmp_path, monkeypatch,pg_log_evidence):
    import delivery_api.app as module
    monkeypatch.delenv('REQUI_PREFLIGHT_GENERATED_AT',raising=False)
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    out = module.debug_preflight(a=type('A', (), {'label': 'u'})())
    assert out == {'startup': None}


import shutil
import subprocess
from pathlib import Path as _Path

import pytest as _pytest

_needs_node = _pytest.mark.skipif(shutil.which('node') is None, reason='needs node')


def _node(source):
    result = subprocess.run(['node', '--input-type=module', '-e', source], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


@_needs_node
def test_the_preflight_report_renders_checked_expected_found_where():
    out = _node("""
      import {formatPreflightReport} from './src/delivery_api/web/debug-digest.js';
      const text = formatPreflightReport({status: 'fail', generated_at: '2026-10-26T19:00:00Z',
        checks: [{name: 'artifacts-root-writable', status: 'pass'},
                 {name: 'database-role-claims', status: 'fail', checked: 'the role/claims round trip', expected: 'a clean transaction', found: 'permission denied', where: 'supabase', remedy: 'grant the role'}]});
      const missing = formatPreflightReport(null);
      console.log(JSON.stringify({text, missing}));
    """)
    rendered = json.loads(out)
    assert 'status REFUSED TO START' in rendered['text']
    assert 'pass artifacts-root-writable' in rendered['text']
    assert 'checked  the role/claims round trip' in rendered['text']
    assert 'found    permission denied' in rendered['text']
    assert 'where    supabase' in rendered['text']
    assert 'remedy   grant the role' in rendered['text']
    assert rendered['missing'] == 'no startup preflight report: this container predates it'


def test_cached_report_freshness_compares_launcher_generation(tmp_path,monkeypatch,pg_log_evidence):
    monkeypatch.setenv('XDG_DATA_HOME',str(tmp_path))
    monkeypatch.setenv('REQUI_PREFLIGHT_GENERATED_AT','current-generation')
    monkeypatch.setenv('REQUI_PREFLIGHT_REPORT_SAVED','0')
    path=tmp_path/'requi-re/logs/preflight-report.json';path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'generated_at':'old-generation','status':'pass'}))
    state=preflight_read.persistence_status()
    assert state['availability']=='stale' and state['write_status']=='failed'
    assert state['expected_generated_at']=='current-generation'
