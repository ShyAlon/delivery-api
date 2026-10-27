"""Startup preflight: refuse to start on provably broken foundations, warn on
degraded ones, one readable report (checked/expected/found/where).

Born from the 2026-10-26 incidents: a container served for hours on a dead
/data mount before the first task died; an owner was locked out by unsettled
compute segments until a hand-run script; failure diagnostics went unrecorded
because a migration was missing; the running build was three merges behind."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('preflight', ROOT / 'scripts' / 'docker' / 'preflight.py')
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class FakeResult:
    def __init__(self, rows): self.rows = list(rows)
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return list(self.rows)


class FakeConn:
    """Substring-scripted connection: the first script key contained in the SQL wins."""
    def __init__(self, script): self.script, self.calls = script, []
    def execute(self, sql, params=None):
        self.calls.append(sql)
        for key, result in self.script.items():
            if key in sql:
                if isinstance(result, Exception): raise result
                return FakeResult(result)
        return FakeResult([])
    def close(self): pass


def fake_import(modules):
    def _import(name):
        if name not in modules: raise ImportError(name)
        return modules[name]
    return _import


def engine_modules(engine_pool_max=4, delivery_pool_max=10):
    import types, inspect
    http = types.SimpleNamespace(create_app=lambda: None, SESSIONS_DSN_ENV='INTERVIEW_ENGINE_SESSIONS_DSN')
    pool = types.SimpleNamespace(ENGINE_POOL_MAX=engine_pool_max, build_pool=lambda dsn, *, max_size=4: None)
    model_config = types.SimpleNamespace(_API_KEY_ENV={'deepseek': 'DEEPSEEK_API_KEY', 'openai': 'OPENAI_API_KEY'})
    db_pool = types.SimpleNamespace(PROCESS_POOL_MAX=delivery_pool_max)
    return {'interview_engine.http_service': http, 'interview_engine.pg_pool': pool,
            'interview_engine.model_config': model_config, 'delivery_api.db_pool': db_pool}


def healthy_env(tmp_path):
    return {'VMODEL_ARTIFACTS_DIR': str(tmp_path / 'artifacts'),
            'DELIVERY_DATABASE_URL': 'postgresql://app@db/example',
            'DELIVERY_CONTROL_PLANE_ONLY': '0', 'DELIVERY_CLOUD_EXECUTION': '0',
            'REQUI_BUILD_REPOS': 'delivery-api=abc12345,interview-engine=def67890',
            'REQUI_EXPECTED_REPOS': 'delivery-api=abc12345,interview-engine=def67890'}


def healthy_pg():
    return lambda dsn: FakeConn({'information_schema': [(t,) for t in preflight.REQUIRED_TABLES],
                                 'current_user': [('delivery_app',)],
                                 'requi_session_active': [(False,)],
                                 'auth.uid()': [(None,)]})


def test_a_healthy_environment_passes(tmp_path):
    report = preflight.run(healthy_env(tmp_path), connect=healthy_pg(), import_module=fake_import(engine_modules()))
    assert report.status == 'pass' and not report.refused
    by_name = {c.name: c for c in report.checks}
    assert by_name['artifacts-root-writable'].status == 'pass'
    assert by_name['database-role-claims'].status == 'pass'
    assert by_name['required-tables'].status == 'pass'
    assert by_name['pool-budget'].status == 'pass'
    assert by_name['provider-key'].status == 'skip'      # execution closed: no key needed
    assert by_name['engine-compatible'].status == 'pass'
    assert by_name['unsettled-compute-segments'].status == 'skip'  # owner-scoped: live in the Debug dialog
    assert by_name['build-staleness'].status == 'pass'


def test_a_dead_artifacts_root_refuses_with_the_cause(tmp_path):
    env = healthy_env(tmp_path); env['VMODEL_ARTIFACTS_DIR'] = str(tmp_path / 'gone' / 'child' / 'artifacts')
    (tmp_path / 'gone').write_text('a file blocks the path')
    report = preflight.run(env, connect=healthy_pg(), import_module=fake_import(engine_modules()))
    check = {c.name: c for c in report.checks}['artifacts-root-writable']
    assert check.status == 'fail' and check.level == 'refuse' and report.refused
    assert 'cannot be created and written' in check.found and 'NotADirectoryError' in check.found


def test_an_unreachable_database_refuses_and_names_it():
    def dead_connect(dsn): raise OSError('connection refused')
    report = preflight.run(healthy_env(Path('/tmp/x-preflight-test')), connect=dead_connect, import_module=fake_import(engine_modules()))
    by_name = {c.name: c for c in report.checks}
    assert by_name['database-role-claims'].status == 'fail' and report.refused
    assert 'connection refused' in by_name['database-role-claims'].found
    assert by_name['required-tables'].status == 'fail'  # no connection: tables cannot be verified


def test_missing_tables_refuse_and_name_the_tables():
    partial = [t for t in preflight.REQUIRED_TABLES if t != 'operator_failure_diagnostics']
    connect = lambda dsn: FakeConn({'information_schema': [(t,) for t in partial], 'current_user': [('delivery_app',)]})
    report = preflight.run(healthy_env(Path('/tmp/x-preflight-test')), connect=connect, import_module=fake_import(engine_modules()))
    check = {c.name: c for c in report.checks}['required-tables']
    assert check.status == 'fail' and 'operator_failure_diagnostics' in check.found
    assert 'migration' in (check.remedy or '')


def test_a_claims_failure_names_the_step():
    conn = FakeConn({'information_schema': [(t,) for t in preflight.REQUIRED_TABLES],
                     'current_user': [('delivery_app',)],
                     'set_config': Exception('permission denied to set role')})
    report = preflight.run(healthy_env(Path('/tmp/x-preflight-test')), connect=lambda dsn: conn, import_module=fake_import(engine_modules()))
    check = {c.name: c for c in report.checks}['database-role-claims']
    assert check.status == 'fail' and 'permission denied' in check.found


def test_pool_budget_over_the_pooler_limit_warns():
    report = preflight.run(healthy_env(Path('/tmp/x-preflight-test')), connect=healthy_pg(),
                           import_module=fake_import(engine_modules(engine_pool_max=10)))
    check = {c.name: c for c in report.checks}['pool-budget']
    assert check.status == 'warn' and '20' in check.found and '15' in check.expected


def test_provider_key_warns_only_when_execution_is_open():
    env = healthy_env(Path('/tmp/x-preflight-test')); env['DELIVERY_CLOUD_EXECUTION'] = '1'
    report = preflight.run(env, connect=healthy_pg(), import_module=fake_import(engine_modules()))
    check = {c.name: c for c in report.checks}['provider-key']
    assert check.status == 'warn' and 'DEEPSEEK_API_KEY' in check.found
    env['OPENAI_API_KEY'] = 'sk-test'
    report = preflight.run(env, connect=healthy_pg(), import_module=fake_import(engine_modules()))
    assert {c.name: c for c in report.checks}['provider-key'].status == 'pass'


def test_an_incompatible_engine_refuses():
    report = preflight.run(healthy_env(Path('/tmp/x-preflight-test')), connect=healthy_pg(),
                           import_module=fake_import({'delivery_api.db_pool': engine_modules()['delivery_api.db_pool']}))
    check = {c.name: c for c in report.checks}['engine-compatible']
    assert check.status == 'fail' and report.refused


def test_a_stale_build_warns_and_a_missing_expectation_skips():
    env = healthy_env(Path('/tmp/x-preflight-test'))
    env['REQUI_EXPECTED_REPOS'] = 'delivery-api=ffff0000,interview-engine=def67890'
    report = preflight.run(env, connect=healthy_pg(), import_module=fake_import(engine_modules()))
    check = {c.name: c for c in report.checks}['build-staleness']
    assert check.status == 'warn' and 'ffff0000' in check.expected and 'abc12345' in check.found
    env.pop('REQUI_EXPECTED_REPOS')
    report = preflight.run(env, connect=healthy_pg(), import_module=fake_import(engine_modules()))
    assert {c.name: c for c in report.checks}['build-staleness'].status == 'skip'


def test_non_postgres_dsn_skips_the_database_checks():
    env = healthy_env(Path('/tmp/x-preflight-test')); env['DELIVERY_DATABASE_URL'] = 'sqlite:////tmp/smoke.db'
    report = preflight.run(env, connect=None, import_module=fake_import(engine_modules()))
    by_name = {c.name: c for c in report.checks}
    assert by_name['database-role-claims'].status == 'skip' and by_name['required-tables'].status == 'skip'
    assert not report.refused


def test_the_report_is_one_readable_checked_expected_found_where_document(tmp_path):
    report = preflight.run(healthy_env(tmp_path), connect=healthy_pg(), import_module=fake_import(engine_modules()))
    text = report.render_text()
    assert 'startup preflight' in text and 'PASS' in text
    for check in report.checks:
        assert check.checked and check.expected and check.found and check.where
    data = json.loads(json.dumps(report.to_dict()))
    assert data['status'] == 'pass' and len(data['checks']) == len(report.checks)


def test_required_tables_match_the_migrations_on_disk():
    expected = preflight.expected_tables_from_migrations(ROOT / 'supabase' / 'migrations')
    assert set(preflight.REQUIRED_TABLES) == expected, 'migration added or renamed a table: update REQUIRED_TABLES'


def test_write_report_never_raises(tmp_path):
    report = preflight.run(healthy_env(tmp_path), connect=healthy_pg(), import_module=fake_import(engine_modules()))
    (tmp_path / 'blocked').write_text('a file, not a directory')
    preflight.write_report(report, tmp_path / 'blocked' / 'preflight-report.json')  # unwritable target: swallowed
    target = tmp_path / 'ok' / 'preflight-report.json'
    preflight.write_report(report, target)
    assert json.loads(target.read_text())['status'] == 'pass'


def test_the_full_image_carries_the_preflight_module():
    dockerfile = (Path(__file__).resolve().parents[1] / 'Dockerfile.full').read_text()
    copy_lines = [line for line in dockerfile.splitlines() if line.startswith('COPY') and 'full_entry.py' in line]
    assert copy_lines and any('preflight.py' in line for line in copy_lines)
