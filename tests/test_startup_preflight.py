"""Startup preflight: refuse to start on provably broken foundations, warn on
degraded ones, one readable report (checked/expected/found/where).

Born from the 2026-10-26 incidents: a container served for hours on a dead
/data mount before the first task died; an owner was locked out by unsettled
compute segments until a hand-run script; failure diagnostics went unrecorded
because a migration was missing; the running build was three merges behind."""
import os
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
    return lambda dsn: FakeConn({'pg_class': [(t, True) for t in preflight.REQUIRED_TABLES],
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
    assert by_name['feature-tables'].status == 'pass'
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
    partial = [t for t in preflight.REQUIRED_TABLES if t != 'phases']   # a core table
    connect = lambda dsn: FakeConn({'pg_class': [(t, True) for t in partial], 'current_user': [('delivery_app',)]})
    report = preflight.run(healthy_env(Path('/tmp/x-preflight-test')), connect=connect, import_module=fake_import(engine_modules()))
    check = {c.name: c for c in report.checks}['required-tables']
    assert check.status == 'fail' and 'phases' in check.found
    assert 'migration' in (check.remedy or '')


def test_a_privilege_filtered_catalog_does_not_refuse_a_healthy_database():
    """2026-10-27, second preflight regression: information_schema.tables hides every
    table the querying login has no grant on, so a healthy database looked 'missing' 11
    feature tables and the container refused to start. Existence must come from
    pg_catalog (never privilege-filtered); visibility is a separate privilege probe."""
    connect = lambda dsn: FakeConn({'information_schema': [],   # privilege-filtered to nothing
                                    'pg_class': [(t, True) for t in preflight.REQUIRED_TABLES],
                                    'current_user': [('delivery_app',)],
                                    'requi_session_active': [(False,)],
                                    'auth.uid()': [(None,)]})
    report = preflight.run(healthy_env(Path('/tmp/x-preflight-test')), connect=connect, import_module=fake_import(engine_modules()))
    by_name = {c.name: c for c in report.checks}
    assert by_name['required-tables'].status == 'pass', by_name['required-tables'].found
    assert by_name['feature-tables'].status == 'pass', by_name['feature-tables'].found
    assert not report.refused


def test_missing_feature_tables_warn_without_refusing_the_start():
    """Payments/operator/expense-guard tables gate optional features: a database that
    never applied those migrations must still start and pass interviews (his standing
    rule: missing optional tables must not block the interview). Warn, name the table,
    its feature, and the exact migration file that creates it."""
    present = [t for t in preflight.REQUIRED_TABLES
               if t not in ('payment_credits', 'payment_events', 'payment_purchases')]
    connect = lambda dsn: FakeConn({'pg_class': [(t, True) for t in present], 'current_user': [('delivery_app',)]})
    report = preflight.run(healthy_env(Path('/tmp/x-preflight-test')), connect=connect, import_module=fake_import(engine_modules()))
    by_name = {c.name: c for c in report.checks}
    assert not report.refused
    assert by_name['required-tables'].status == 'pass'
    check = by_name['feature-tables']
    assert check.status == 'warn'
    assert 'payment_credits' in check.found and 'payment_purchases' in check.found
    assert '20261010090000_private_payment_ledger.sql' in (check.found + (check.remedy or ''))


def test_a_present_but_unreadable_core_table_refuses_with_the_grant():
    rows = [(t, t != 'phases') for t in preflight.REQUIRED_TABLES]
    connect = lambda dsn: FakeConn({'pg_class': rows, 'current_user': [('delivery_app',)]})
    report = preflight.run(healthy_env(Path('/tmp/x-preflight-test')), connect=connect, import_module=fake_import(engine_modules()))
    check = {c.name: c for c in report.checks}['required-tables']
    assert check.status == 'fail' and report.refused
    assert 'phases' in check.found and 'requi_backend' in check.found
    assert 'GRANT' in (check.remedy or '')


def test_a_present_but_unreadable_feature_table_warns():
    rows = [(t, t != 'payment_credits') for t in preflight.REQUIRED_TABLES]
    connect = lambda dsn: FakeConn({'pg_class': rows, 'current_user': [('delivery_app',)]})
    report = preflight.run(healthy_env(Path('/tmp/x-preflight-test')), connect=connect, import_module=fake_import(engine_modules()))
    by_name = {c.name: c for c in report.checks}
    assert not report.refused and by_name['feature-tables'].status == 'warn'
    assert 'payment_credits' in by_name['feature-tables'].found


def test_feature_tables_map_to_required_tables_and_migrations_that_create_them():
    for table, (migration, feature) in preflight.FEATURE_TABLES.items():
        assert table in preflight.REQUIRED_TABLES, table
        path = ROOT / 'supabase' / 'migrations' / migration
        assert path.is_file(), migration
        assert table in preflight._CREATE_TABLE.findall(path.read_text(errors='replace')), (table, migration)
        assert feature.strip(), table


def test_provider_key_with_a_keyless_provider_does_not_crash():
    """The engine's _API_KEY_ENV maps providers with no key (ollama) to None; the old
    code fed None into os.environ.get and the whole preflight check crashed into the
    unnamed 'unknown' fallback (2026-10-27: [warn] unknown, TypeError: str expected,
    not NoneType). Keyless providers contribute nothing, not a crash."""
    import types
    modules = engine_modules()
    modules['interview_engine.model_config'] = types.SimpleNamespace(
        _API_KEY_ENV={'deepseek': 'DEEPSEEK_API_KEY', 'ollama': None})
    env = healthy_env(Path('/tmp/x-preflight-test')); env['DELIVERY_CLOUD_EXECUTION'] = '1'
    report = preflight.run(env, connect=healthy_pg(), import_module=fake_import(modules))
    check = {c.name: c for c in report.checks}['provider-key']
    assert check.status == 'warn' and 'DEEPSEEK_API_KEY' in check.found and 'None' not in check.found
    env['DEEPSEEK_API_KEY'] = 'sk-test'
    report = preflight.run(env, connect=healthy_pg(), import_module=fake_import(modules))
    assert {c.name: c for c in report.checks}['provider-key'].status == 'pass'


def test_a_crashing_check_names_itself_not_unknown():
    """The crash fallback must name the check that died ('unknown' told him nothing
    when provider-key crashed on 2026-10-27)."""
    env = healthy_env(Path('/tmp/x-preflight-test')); env['REQUI_EXPECTED_REPOS'] = 123  # int: .strip() raises
    report = preflight.run(env, connect=healthy_pg(), import_module=fake_import(engine_modules()))
    by_name = {c.name: c for c in report.checks}
    assert 'unknown' not in by_name
    check = by_name['build-staleness']
    assert check.status == 'warn' and 'the check itself failed' in check.found and 'AttributeError' in check.found


def test_a_claims_failure_names_the_step():
    conn = FakeConn({'pg_class': [(t, True) for t in preflight.REQUIRED_TABLES],
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
    assert by_name['feature-tables'].status == 'skip'
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


def _fake_engine_modules(monkeypatch):
    import types
    import inspect as _inspect
    package = types.ModuleType('interview_engine')          # a real package re-exports nothing
    http = types.ModuleType('interview_engine.http_service')
    http.create_app = lambda: None
    http.SESSIONS_DSN_ENV = 'INTERVIEW_ENGINE_SESSIONS_DSN'
    pool = types.ModuleType('interview_engine.pg_pool')
    pool.ENGINE_POOL_MAX = 4
    def build_pool(dsn, *, max_size=4, name='engine'):
        return None
    pool.build_pool = build_pool
    monkeypatch.setitem(sys.modules, 'interview_engine', package)
    monkeypatch.setitem(sys.modules, 'interview_engine.http_service', http)
    monkeypatch.setitem(sys.modules, 'interview_engine.pg_pool', pool)


def test_the_default_import_path_resolves_submodules(monkeypatch, tmp_path):
    """2026-10-27: __import__('a.b') returns the TOP package; the preflight's real
    import_module must resolve the submodule, or check 6 refuses every real start
    while every mocked test stays green (the smoke broke silently for exactly this)."""
    _fake_engine_modules(monkeypatch)
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR', str(tmp_path))
    monkeypatch.setenv('DELIVERY_DATABASE_URL', 'sqlite:///' + str(tmp_path / 's.db'))
    report = preflight.run(os.environ)   # no import_module override: the REAL default
    engine = next(c for c in report.checks if c.name == 'engine-compatible')
    assert engine.status == 'pass', engine.found
    budget = next(c for c in report.checks if c.name == 'pool-budget')
    assert 'import failed' not in budget.found


def test_an_engine_check_failure_names_the_missing_piece(monkeypatch):
    import types
    package = types.ModuleType('interview_engine')
    http = types.ModuleType('interview_engine.http_service')
    http.SESSIONS_DSN_ENV = 'INTERVIEW_ENGINE_SESSIONS_DSN'   # create_app missing
    monkeypatch.setitem(sys.modules, 'interview_engine', package)
    monkeypatch.setitem(sys.modules, 'interview_engine.http_service', http)
    check = preflight._check_engine({}, preflight.importlib.import_module)
    assert check.status == 'fail' and 'create_app' in check.found


def test_the_launcher_pins_the_expected_build_for_the_staleness_check():
    script = (Path(__file__).resolve().parents[1] / 'scripts' / 'ops' / 'preview.sh').read_text()
    assert script.count('REQUI_EXPECTED_REPOS=%s') == 2   # both preview paths (local settings and server settings)


def test_child_pool_budget_counts_active_child_and_no_compatibility():
    result=preflight._check_pool_budget({'DELIVERY_CHILD_EXECUTION':'local','DELIVERY_LOCAL_PREVIEW':'1'},__import__('importlib').import_module)
    assert '8 parent + 4 active child + 0 compatibility' in result.found


def test_migrated_private_tables_use_their_intended_roles_not_backend_select():
    import psycopg
    from test_postgres_store import DSN
    assert DSN,'isolated migrated Postgres is required for this regression'
    with psycopg.connect(DSN.replace('user=sandbox','user=delivery_app')) as conn:
        conn.execute('SET ROLE requi_backend')
        result=preflight._check_feature_tables({'DELIVERY_DATABASE_URL':'postgresql://test'},[conn],preflight.REQUIRED_TABLES)
    assert result.status=='pass',result.found
    assert 'GRANT SELECT' not in (result.remedy or '')


def test_intended_role_policy_names_every_exception_from_migrations():
    assert preflight.TABLE_ACCESS['payment_events']=='requi_payment'
    assert preflight.TABLE_ACCESS['operator_state_events']=='requi_operator_read'
    assert preflight.TABLE_ACCESS['operator_principals'] is None
    assert preflight.TABLE_ACCESS['operator_failure_diagnostics']=='requi_backend'


def test_real_optional_role_grant_loss_and_private_exposure_are_named():
    import psycopg
    from test_postgres_store import DSN
    with psycopg.connect(DSN) as conn:
        try:
            # Isolated test database only; rollback restores the migration ACLs.
            conn.execute('REVOKE SELECT ON public.payment_events FROM requi_payment')
            conn.execute('GRANT SELECT ON public.operator_principals TO requi_backend')
            result=preflight._check_feature_tables({'DELIVERY_DATABASE_URL':'postgresql://test'},[conn],preflight.REQUIRED_TABLES)
            assert result.status=='warn'
            assert 'payment_events (expected SELECT by requi_payment' in result.found
            assert 'operator_principals (owner/function-only' in result.found
            assert 'must not have direct SELECT' in result.found
            assert 'blanket' in result.remedy and 'GRANT SELECT ON' not in result.remedy
        finally:conn.rollback()


def test_missing_diagnostics_still_warns_with_exact_migration():
    import psycopg
    from test_postgres_store import DSN
    with psycopg.connect(DSN) as conn:
        try:
            conn.execute('ALTER TABLE public.operator_failure_diagnostics RENAME TO hidden_diagnostics')
            result=preflight._check_feature_tables({'DELIVERY_DATABASE_URL':'postgresql://test'},[conn],preflight.REQUIRED_TABLES)
            assert result.status=='warn'
            assert 'operator_failure_diagnostics (20261025120000_operator_failure_diagnostics.sql)' in result.found
            assert 'payment_' not in result.found
        finally:conn.rollback()


def test_unknown_optional_role_does_not_crash_the_table_probe():
    # A missing role resolves to NULL in the pg_roles join, before the privilege
    # function is evaluated. Assert the actual PG evaluation, not a fake bool.
    import psycopg
    from test_postgres_store import DSN
    policy=preflight.TABLE_ACCESS.copy()
    try:
        preflight.TABLE_ACCESS['payment_events']='nonexistent_preflight_role'
        with psycopg.connect(DSN.replace('user=sandbox','user=delivery_app')) as conn:
            result=preflight._check_feature_tables({'DELIVERY_DATABASE_URL':'postgresql://test'},[conn],preflight.REQUIRED_TABLES)
        assert result.status=='warn' and 'nonexistent_preflight_role' in result.found
        assert 'payment_events' in result.found and 'operator_principals' not in result.found
    finally:preflight.TABLE_ACCESS.clear();preflight.TABLE_ACCESS.update(policy)


def test_report_write_failure_warns_and_retains_stdout_authority(tmp_path,capsys):
    blocker=tmp_path/'blocked';blocker.write_text('file')
    report=preflight.Report(generated_at='now')
    assert preflight.write_report(report,blocker/'report.json') is False
    text=capsys.readouterr().err
    assert 'cached Debug report was not updated' in text and 'FileExistsError' in text
    assert str(blocker) not in text
    assert preflight.write_report(report,tmp_path/'ok.json') is True


@pytest.mark.parametrize('builder,name,level', [
    ('_check_artifacts', 'artifacts-root-writable', 'refuse'),
    ('_check_database', 'database-role-claims', 'refuse'),
    ('_check_process_logs', 'process-log-store', 'refuse'),
    ('_check_tables', 'required-tables', 'refuse'),
    ('_check_feature_tables', 'feature-tables', 'warn'),
    ('_check_pool_budget', 'pool-budget', 'warn'),
    ('_check_provider_key', 'provider-key', 'warn'),
    ('_check_engine', 'engine-compatible', 'refuse'),
    ('_check_segments', 'unsettled-compute-segments', 'warn'),
    ('_check_staleness', 'build-staleness', 'warn'),
])
def test_check_crashes_preserve_safety_level_and_finish_report(tmp_path, monkeypatch, builder, name, level):
    """Row 12-01: unverified foundations refuse; optional checks still only warn."""
    class TrackedConn(FakeConn):
        closed = False
        def close(self): self.closed = True
    conn = TrackedConn(healthy_pg()('ignored').script)
    def crash(*args): raise RuntimeError('injected check failure')
    monkeypatch.setattr(preflight, builder, crash)
    report = preflight.run(healthy_env(tmp_path), connect=lambda dsn: conn,
                           import_module=fake_import(engine_modules()))
    assert len(report.checks) == 10
    check = next(c for c in report.checks if c.name == name)
    assert check.level == level
    assert check.status == ('fail' if level == 'refuse' else 'warn')
    assert report.refused == (level == 'refuse')
    assert report.status == ('fail' if level == 'refuse' else 'warn')
    assert 'the check itself failed: RuntimeError' in check.found
    assert 'injected check failure' not in check.found
    assert check.checked and check.expected and check.where and check.remedy
    serialized = next(c for c in report.to_dict()['checks'] if c['name'] == name)
    assert serialized['level'] == level and serialized['status'] == check.status
    assert ('REFUSING TO START' if level == 'refuse' else 'PASS WITH WARNINGS') in report.render_text()
    assert next(c for c in report.checks if c.name == 'build-staleness')
    if builder != '_check_database': assert conn.closed


def test_crash_report_never_echoes_untrusted_exception_details(tmp_path, monkeypatch):
    secret = 'postgresql://user:fake-password@db/example?token=fake-token'
    def crash(*args): raise ValueError(secret)
    monkeypatch.setattr(preflight, '_check_engine', crash)
    report = preflight.run(healthy_env(tmp_path), connect=healthy_pg(),
                           import_module=fake_import(engine_modules()))
    assert secret not in report.render_text()
    assert secret not in json.dumps(report.to_dict())
    check = next(c for c in report.checks if c.name == 'engine-compatible')
    assert 'ValueError' in check.found and check.level == 'refuse' and check.status == 'fail'


@pytest.mark.parametrize('builder,exit_code', [('_check_engine', 2), ('_check_feature_tables', 0)])
def test_main_exit_code_and_persisted_report_follow_crash_level(tmp_path, monkeypatch, capsys, builder, exit_code):
    real_run = preflight.run
    def crash(*args): raise RuntimeError('injected')
    monkeypatch.setattr(preflight, builder, crash)
    monkeypatch.setattr(preflight, 'run', lambda env: real_run(healthy_env(tmp_path),
                        connect=healthy_pg(), import_module=fake_import(engine_modules())))
    path = tmp_path / 'report.json'
    monkeypatch.setattr(preflight, 'report_path', lambda env: path)
    assert preflight.main() == exit_code
    assert json.loads(path.read_text())['status'] == ('fail' if exit_code else 'warn')
    assert ('REFUSING TO START' if exit_code else 'PASS WITH WARNINGS') in capsys.readouterr().out


def test_pg_process_log_preflight_validates_stable_source_and_writer_access(tmp_path):
    env=healthy_env(tmp_path)
    env['DELIVERY_LOG_SOURCE']=''
    report=preflight.run(env,connect=healthy_pg(),import_module=fake_import(engine_modules()))
    check={c.name:c for c in report.checks}['process-log-store']
    assert check.status=='fail' and check.level=='refuse' and report.refused
    env.pop('DELIVERY_LOG_SOURCE')
    seen=[]
    def connect(dsn):
        c=healthy_pg()(dsn);seen.append(c);return c
    report=preflight.run(env,connect=connect,import_module=fake_import(engine_modules()))
    assert {c.name:c for c in report.checks}['process-log-store'].status=='pass'
    assert any('SET LOCAL ROLE requi_log_writer' in sql for c in seen for sql in c.calls)
    assert any('operator_startup_reports' in sql for c in seen for sql in c.calls)
    assert not any('INSERT ' in sql or 'DELETE ' in sql for c in seen for sql in c.calls)
