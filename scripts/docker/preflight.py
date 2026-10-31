"""Startup preflight for the full service: refuse to start on provably broken
foundations, warn on degraded ones, and say it all in ONE readable report
(checked / expected / found / where) instead of a raw traceback hours later.

Deterministic checks only: no model calls, no spend, nothing invented. Database
checks are read-only SELECTs run as the app's own restricted login - never DDL,
never a write. Hard failures (refuse): artifacts root, database role/claims,
required (core) tables, engine compatibility. Warnings: feature-gated tables
(missing optional tables must never block the interview), pool budget, provider
key, unsettled compute segments (owner-scoped: evaluated live in the Debug
dialog), build staleness. Run by full_entry before any process is spawned; the report is
also written next to the engine log for the Debug dialog."""
import importlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

POOLER_CLIENT_BUDGET = 15   # Supabase pooler, session mode. One shared budget: delivery pool + engine pool.
SYNTHETIC_SUB = '00000000-0000-0000-0000-000000000000'

# Every table the migrations create (public schema). Pinned by
# tests/test_startup_preflight.py::test_required_tables_match_the_migrations_on_disk:
# a migration that adds or renames a table fails that test until this set is updated.
REQUIRED_TABLES = frozenset({
    'admission_limits', 'admission_totals', 'admission_total_entries', 'answer_feedback', 'artifact_bytes', 'artifact_manifest_ready', 'artifacts',
    'audit_log', 'checkpoint_blobs', 'checkpoint_migrations', 'checkpoint_writes', 'checkpoints',
    'closed_revision_acceptance_revocations', 'closed_revision_acceptances',
    'closed_revision_install_events', 'closed_revision_installations', 'closed_revision_lineage_plans',
    'closed_revision_promotion_events', 'closed_revision_restart_intents', 'closed_revision_source_reviews',
    'credit_accounts', 'credit_adjustments', 'credit_charges', 'credit_ledger', 'decision_drafts',
    'decision_reviews', 'engine_session_journal', 'engine_sessions', 'events', 'expense_alert_outbox',
    'expense_guard_episodes', 'expense_guard_policy', 'own_test_expense_profiles', 'final_packages', 'gates', 'installed_execution_bindings', 'links',
    'operator_failure_diagnostics', 'operator_principals', 'operator_state_events', 'payment_credits',
    'payment_events', 'payment_purchases', 'phases', 'producer_results', 'progress_ingest', 'progress_seen',
    'provider_attempts', 'retail_compute_segments', 'retail_contributions', 'retail_scale_migrations',
    'review_comments', 'runs', 'staged_provider_responses', 'task_artifacts', 'tasks', 'usage',
    'user_task_policy', 'user_task_policy_changes', 'users', 'worker_jobs',
    'generator_documents','generator_snapshots','resource_run_samples',
    'resource_container_summaries','resource_run_summaries','resource_container_samples','engine_quality_config',
})

# Tables that gate OPTIONAL features only: payments, the operator console, the
# confirmed expense guard, credit-adjustment evidence, retail scale, task-policy
# suspension. A database that never applied these migrations must still start and
# serve interviews (a missing optional table must never block the interview), so
# these are warn-level, each named with the migration file that creates it.
# Pinned by tests: every entry sits in REQUIRED_TABLES and its migration creates it.
FEATURE_TABLES = {
    'credit_adjustments': ('20261009130000_private_credit_adjustment_evidence.sql',
                           'private credit-adjustment evidence (operator grant credits)'),
    'expense_guard_episodes': ('20261009213000_confirmed_expense_guard.sql', 'the confirmed expense guard'),
    'expense_guard_policy': ('20261009213000_confirmed_expense_guard.sql', 'the confirmed expense guard'),
    'operator_failure_diagnostics': ('20261025120000_operator_failure_diagnostics.sql', 'operator failure diagnostics'),
    'operator_principals': ('20261009104000_operator_read_registry.sql', 'the operator console'),
    'operator_state_events': ('20261009112000_operator_state_event_log.sql', 'the operator event log'),
    'payment_credits': ('20261010090000_private_payment_ledger.sql', 'payments'),
    'payment_events': ('20261010090000_private_payment_ledger.sql', 'payments'),
    'payment_purchases': ('20261010090000_private_payment_ledger.sql', 'payments'),
    'retail_scale_migrations': ('20261010190000_exact_retail_contributions.sql', 'retail scale contributions'),
    'user_task_policy_changes': ('20261009133000_task_operation_suspension.sql', 'task-operation suspension policy'),
}

# Explicit access contract from the migrations. None means owner/function-only:
# the normal backend must NOT have a direct SELECT grant. Do not repair these
# intentional denials with a blanket grant. Other tables use the backend.
TABLE_ACCESS = {table: 'requi_backend' for table in REQUIRED_TABLES}
TABLE_ACCESS.update({
    'engine_quality_config': 'requi_engine_config_read',
    'resource_run_samples': 'requi_metrics_writer',
    'resource_run_summaries': 'requi_metrics_writer',
    'resource_container_samples': 'requi_metrics_writer',
    'resource_container_summaries': 'requi_metrics_writer',
    'payment_purchases': 'requi_payment',
    'payment_credits': 'requi_payment',
    'payment_events': 'requi_payment',
    'operator_state_events': 'requi_operator_read',
    'credit_adjustments': None,
    'expense_guard_policy': None,
    'own_test_expense_profiles': None,
    'expense_guard_episodes': None,
    'operator_principals': None,
    'retail_scale_migrations': None,
    'user_task_policy_changes': None,
})


_CREATE_TABLE = re.compile(r'create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?([a-z_][a-z0-9_]*)', re.I)


def expected_tables_from_migrations(migrations_dir):
    """The tables the checked-in migrations create. The drift test pins REQUIRED_TABLES to this."""
    found = set()
    for path in sorted(Path(migrations_dir).glob('*.sql')):
        found.update(_CREATE_TABLE.findall(path.read_text(errors='replace')))
    return found


@dataclass
class Check:
    name: str
    level: str       # 'refuse' or 'warn'
    status: str      # 'pass', 'fail', 'warn', 'skip'
    checked: str
    expected: str
    found: str
    where: str
    remedy: str = None


@dataclass
class Report:
    checks: list = field(default_factory=list)
    generated_at: str = ''

    @property
    def refused(self):
        return any(c.level == 'refuse' and c.status == 'fail' for c in self.checks)

    @property
    def status(self):
        if self.refused: return 'fail'
        if any(c.status in ('fail', 'warn') for c in self.checks): return 'warn'
        return 'pass'

    def render_text(self):
        headline = {'pass': 'startup preflight: PASS', 'warn': 'startup preflight: PASS WITH WARNINGS',
                    'fail': 'startup preflight: REFUSING TO START'}[self.status]
        lines = [headline]
        for c in self.checks:
            line = '[%s] %s: checked %s; expected %s; found %s; where %s' % (
                c.status, c.name, c.checked, c.expected, c.found, c.where)
            if c.remedy and c.status in ('fail', 'warn'): line += '; remedy: ' + c.remedy
            lines.append(line)
        return '\n'.join(lines)

    def to_dict(self):
        return {'generated_at': self.generated_at, 'status': self.status,
                'checks': [{'name': c.name, 'level': c.level, 'status': c.status, 'checked': c.checked,
                            'expected': c.expected, 'found': c.found, 'where': c.where, 'remedy': c.remedy}
                           for c in self.checks]}


def report_path(env):
    """Next to the engine log (mirrors delivery_api.config.default_data_dir), so the
    Debug dialog reads back the same file the container wrote."""
    base = env.get('XDG_DATA_HOME')
    root = (Path(base).expanduser() if base else Path.home() / '.local' / 'share') / 'requi-re'
    return root / 'logs' / 'preflight-report.json'


def write_report(report, path):
    """Best effort: the stdout report is the primary channel; never fail the start on this."""
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(report.to_dict(), indent=2))
        os.replace(tmp, path)
        return True
    except Exception as error:
        print('WARNING: cached Debug report was not updated (%s); stdout preflight is authoritative.' % type(error).__name__,file=sys.stderr)
        return False


def artifacts_root_problem(env):
    """The engine's stop fence and every artifact write need a creatable, writable artifacts
    root. A dead data mount otherwise stays invisible until the first interview pays for it
    (2026-10-26: a container served for hours on a stale mount; the first engine start died
    with FileNotFoundError and the task page showed only 'session unavailable'). Returns the
    problem text, or None when ready."""
    root = env.get('VMODEL_ARTIFACTS_DIR')
    if not root: return 'VMODEL_ARTIFACTS_DIR is not set; the image sets it to /data/artifacts'
    try:
        path = Path(root); path.mkdir(parents=True, exist_ok=True)
        probe = path / '.requi-write-probe'
        probe.write_text('ok'); probe.unlink()
    except OSError as error:
        return 'the artifacts root %s cannot be created and written (%s); the data volume is missing or stale - recreate the container with a fresh data mount' % (root, type(error).__name__)
    return None


def _check_artifacts(env):
    problem = artifacts_root_problem(env)
    root = env.get('VMODEL_ARTIFACTS_DIR') or '(unset)'
    return Check('artifacts-root-writable', 'refuse', 'fail' if problem else 'pass',
                 'that the artifacts root can be created and written (mkdir -p + a real write probe)',
                 'a writable directory', problem or 'writable: %s' % root, root,
                 None if not problem else 'recreate the container with a fresh data mount')


def _pg_connect(connect, dsn):
    return connect(dsn)


def _check_database(env, connect, connection_holder):
    dsn = env.get('DELIVERY_DATABASE_URL', '')
    if not dsn.startswith(('postgresql://', 'postgres://')):
        return Check('database-role-claims', 'refuse', 'fail', 'a PostgreSQL DELIVERY_DATABASE_URL with a working role + claims round trip',
                     'connect, SET ROLE requi_backend, set tenant claims, and probe a tenant table without error',
                     'required PostgreSQL DSN missing or unsupported', 'DELIVERY_DATABASE_URL')
    try:
        connection = _pg_connect(connect, dsn)
        connection_holder.append(connection)
    except Exception as error:
        return Check('database-role-claims', 'refuse', 'fail', 'a PostgreSQL DELIVERY_DATABASE_URL with a working role + claims round trip',
                     'connect, SET ROLE requi_backend, set tenant claims, and probe a tenant table without error',
                     'cannot connect: %s: %s' % (type(error).__name__, str(error)[:200]), 'DELIVERY_DATABASE_URL',
                     'check the database is reachable from this host and the app login is valid')
    try:
        user = connection.execute('SELECT current_user').fetchone()[0]
        connection.execute("SELECT set_config('role','requi_backend',false)")
        connection.execute("SELECT set_config('request.jwt.claim.sub',%s,false)", (SYNTHETIC_SUB,))
        connection.execute("SELECT set_config('request.jwt.claim.session_id',%s,false)", (SYNTHETIC_SUB,))
        connection.execute('SELECT public.requi_session_active()').fetchone()
        connection.execute('SELECT auth.uid()').fetchone()
        connection.execute('SELECT 1 FROM public.tasks LIMIT 1').fetchone()
        return Check('database-role-claims', 'refuse', 'pass', 'a PostgreSQL DELIVERY_DATABASE_URL with a working role + claims round trip',
                     'connect, SET ROLE requi_backend, set tenant claims, and probe a tenant table without error',
                     'connected as %s; role and claims accepted; tenant probe executed' % user, 'DELIVERY_DATABASE_URL')
    except Exception as error:
        return Check('database-role-claims', 'refuse', 'fail', 'a PostgreSQL DELIVERY_DATABASE_URL with a working role + claims round trip',
                     'connect, SET ROLE requi_backend, set tenant claims, and probe a tenant table without error',
                     'the role/claims round trip failed: %s: %s' % (type(error).__name__, str(error)[:200]),
                     'SET ROLE requi_backend + request.jwt.claim.* on the app connection',
                     'the app login must be able to SET ROLE requi_backend; row security hides tenant rows without claims')


def _check_process_logs(env,connect):
    # Local ephemeral evidence is optional, never a PG schema/role dependency.
    return Check('process-log-store','warn','pass','ephemeral bounded server file logs',
                 'asynchronous file writer; no database connection','file sink selected',
                 'Debug dialog / server files')


def _check_engine_quality(env,connect):
    if not env.get('DELIVERY_DATABASE_URL','').startswith(('postgres://','postgresql://','host=')):
        return Check('engine-quality-store','refuse','skip','durable engine quality configuration','PG runtime','legacy reference profile','DELIVERY_DATABASE_URL')
    try:
        if env.get('INTERVIEW_ENGINE_QUESTION_QUALITY_CONFIG'):
            raise ValueError('legacy quality file override is not allowed in PG runtime')
        connection=_pg_connect(connect,env.get('DELIVERY_DATABASE_URL',''))
        try:
            connection.execute('SET LOCAL ROLE requi_engine_config_read')
            connection.execute("SET LOCAL statement_timeout='1000ms'")
            row=connection.execute("SELECT body FROM public.engine_quality_config WHERE config_key='question-quality'").fetchone()
            if row:
                from interview_engine.question_config import validate
                validate(row[0])
        finally:connection.close()
        return Check('engine-quality-store','refuse','pass','durable engine quality configuration',
                     'restricted PG read role and fixed settings table','read probe passed','requi_engine_config_read')
    except Exception as error:
        return Check('engine-quality-store','refuse','fail','durable engine quality configuration',
                     'restricted PG read role and fixed settings table','unavailable: '+type(error).__name__,
                     'requi_engine_config_read','apply engine quality migration and grant app login SET membership; remove legacy file override')


def _check_resource_metrics(env,connect):
    dsn=env.get('DELIVERY_DATABASE_URL','')
    try:
        from delivery_api.run_metrics import selected_metrics
        repo=selected_metrics(dsn,env)
        if repo is None:return Check('resource-metrics-store','refuse','skip','durable resource metrics','source-bound PG writer','legacy profile','DELIVERY_DATABASE_URL')
        connection=_pg_connect(connect,dsn)
        try:
            connection.execute('SET LOCAL ROLE requi_metrics_writer')
            connection.execute("SET LOCAL statement_timeout='1000ms'")
            connection.execute("SELECT set_config('requi.metrics_source',%s,true)",(repo.source_id,))
            if not connection.execute("SELECT has_function_privilege(current_user,'public.requi_maintain_resource_metrics(double precision,double precision)','EXECUTE')").fetchone()[0]:
                raise ValueError('metrics maintenance unavailable')
            for table in ('resource_run_samples','resource_run_summaries','resource_container_samples','resource_container_summaries'):
                connection.execute('SELECT 1 FROM public.'+table+' LIMIT 0')
        finally:connection.close()
        return Check('resource-metrics-store','refuse','pass','durable resource metrics','stable host series and restricted PG role/tables','writer read probes passed','DELIVERY_METRICS_SOURCE; requi_metrics_writer')
    except Exception as error:
        return Check('resource-metrics-store','refuse','fail','durable resource metrics','stable host series and restricted PG role/tables',
                     'unavailable: '+type(error).__name__,'DELIVERY_METRICS_SOURCE; requi_metrics_writer',
                     'use PostgreSQL metrics, apply metrics migration, grant app login SET membership in requi_metrics_writer')


def _table_visibility(connection_holder):
    """Catalog existence and intended access, never privilege-filtered existence.

    Only fixed application table/role literals enter the VALUES list. Resolve
    pg_roles before probing: a missing optional role is a failed feature check,
    not an exception that masks every other table.
    """
    policy = ','.join("('%s',%s)" % (table, "NULL" if role is None else "'%s'" % role)
                      for table, role in sorted(TABLE_ACCESS.items()))
    rows = connection_holder[0].execute(
        "SELECT c.relname, CASE WHEN policy.role_name IS NULL THEN "
        "NOT has_table_privilege('requi_backend', c.oid, 'SELECT') "
        "WHEN r.oid IS NULL THEN false ELSE has_table_privilege(r.oid, c.oid, 'SELECT') END "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN (VALUES " + policy + ") AS policy(table_name,role_name) ON policy.table_name=c.relname "
        "LEFT JOIN pg_roles r ON r.rolname=policy.role_name "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r','p')").fetchall()
    return {r[0]: bool(r[1]) for r in rows}


def _access_problem(table):
    role=TABLE_ACCESS[table]
    if role is None:return '%s (owner/function-only; requi_backend must not have direct SELECT)' % table
    return '%s (expected SELECT by %s; verify role exists and its migration grants)' % (table,role)


def _table_problems(tables, visible):
    missing = sorted(t for t in tables if t not in visible)
    unreadable = sorted(t for t in tables if t in visible and not visible[t])
    return missing, unreadable


def _check_tables(env, connection_holder, required_tables):
    core = sorted(set(required_tables) - set(FEATURE_TABLES))
    checked = 'that every core table the interview requires exists in the public schema with its intended role access'
    expected = '%d core tables from the checked-in migrations (feature-gated tables are the feature-tables check)' % len(core)
    if not env.get('DELIVERY_DATABASE_URL', '').startswith(('postgresql://', 'postgres://')):
        return Check('required-tables', 'refuse', 'skip', checked, expected,
                     'skipped: the DSN is not PostgreSQL', 'pg_catalog (pg_class/pg_namespace)')
    if not connection_holder:
        return Check('required-tables', 'refuse', 'fail', checked, expected,
                     'cannot verify: no database connection (see database-role-claims)', 'pg_catalog (pg_class/pg_namespace)',
                     'fix the database connection first')
    missing, unreadable = _table_problems(core, _table_visibility(connection_holder))
    if missing or unreadable:
        found = []
        if missing: found.append('missing from the database: %s' % ', '.join(missing))
        if unreadable: found.append('intended access mismatch: %s' % ', '.join(_access_problem(t) for t in unreadable))
        remedy = []
        if missing: remedy.append('apply the missing migrations (supabase/migrations) to this database')
        if unreadable: remedy.append('review GRANT/REVOKE for each named table against its checked-in migration and intended role; do not add broad grants')
        return Check('required-tables', 'refuse', 'fail', checked, expected,
                     '; '.join(found), 'pg_catalog (pg_class/pg_namespace)', '; '.join(remedy))
    return Check('required-tables', 'refuse', 'pass', checked, expected,
                 'all %d present with intended role access' % len(core), 'pg_catalog (pg_class/pg_namespace)')


def _check_feature_tables(env, connection_holder, required_tables):
    feature = sorted(set(required_tables) & set(FEATURE_TABLES))
    checked = 'the feature-gated tables (payments, operator console, expense guard, credit evidence, retail scale)'
    expected = '%d optional feature tables present; a missing one disables only its feature, never the interview' % len(feature)
    if not env.get('DELIVERY_DATABASE_URL', '').startswith(('postgresql://', 'postgres://')):
        return Check('feature-tables', 'warn', 'skip', checked, expected,
                     'skipped: the DSN is not PostgreSQL', 'pg_catalog (pg_class/pg_namespace)')
    if not connection_holder:
        return Check('feature-tables', 'warn', 'skip', checked, expected,
                     'skipped: no database connection (see database-role-claims)', 'pg_catalog (pg_class/pg_namespace)')
    missing, unreadable = _table_problems(feature, _table_visibility(connection_holder))
    if missing or unreadable:
        found = []
        if missing:
            found.append('missing: ' + ', '.join('%s (%s)' % (t, FEATURE_TABLES[t][0]) for t in missing))
        if unreadable:
            found.append('intended access mismatch: %s' % ', '.join(_access_problem(t) for t in unreadable))
        remedy = ('optional features only - the interview runs without them; to enable one, apply its named '
                  'migration to this database (supabase/migrations); review named access mismatches against the intended '
                  'role and migration, not blanket backend grants')
        return Check('feature-tables', 'warn', 'warn', checked, expected,
                     '; '.join(found), 'pg_catalog (pg_class/pg_namespace)', remedy)
    return Check('feature-tables', 'warn', 'pass', checked, expected,
                 'all %d present with intended role access (private tables stay owner/function-only)' % len(feature), 'pg_catalog (pg_class/pg_namespace)')


def _check_pool_budget(env, import_module):
    budget = int(env.get('REQUI_POOLER_CLIENT_BUDGET', POOLER_CLIENT_BUDGET))
    try:
        delivery_max = import_module('delivery_api.db_pool').PROCESS_POOL_MAX
    except Exception as error:
        delivery_max = None
    try:
        engine_max = import_module('interview_engine.pg_pool').ENGINE_POOL_MAX
    except Exception:
        engine_max = None
    if env.get('DELIVERY_CHILD_EXECUTION'):
        try:
            from delivery_api.child_pool_budget import child_mode
            if child_mode(env):
                delivery_max=8;engine_max=4
        except ValueError as error:
            return Check('pool-budget','refuse','fail','valid local child selector',
                         'local opt-in only',str(error),'DELIVERY_CHILD_EXECUTION / DELIVERY_LOCAL_PREVIEW')
    found = 'delivery pool %d + engine pool %d = %d clients' % (delivery_max or 0, engine_max or 0, (delivery_max or 0) + (engine_max or 0))
    if env.get('DELIVERY_CHILD_EXECUTION')=='local':found += ' (8 parent + 4 active child + 0 compatibility)'
    if delivery_max is None or engine_max is None:
        return Check('pool-budget', 'warn', 'warn', 'the configured process pools against the pooler client limit',
                     'delivery pool + engine pool <= %d clients (Supabase pooler, session mode)' % budget,
                     'cannot determine one pool size (import failed) - engine compatibility check covers a missing engine',
                     'delivery_api.db_pool.PROCESS_POOL_MAX and interview_engine.pg_pool.ENGINE_POOL_MAX')
    if delivery_max + engine_max > budget:
        return Check('pool-budget', 'warn', 'warn', 'the configured process pools against the pooler client limit',
                     'delivery pool + engine pool <= %d clients (Supabase pooler, session mode)' % budget,
                     found + ' - over the limit; bursts will queue at checkout', 'db_pool / pg_pool configuration',
                     'lower a pool size or raise the pooler limit')
    return Check('pool-budget', 'warn', 'pass', 'the configured process pools against the pooler client limit',
                 'delivery pool + engine pool <= %d clients (Supabase pooler, session mode)' % budget,
                 found, 'db_pool / pg_pool configuration')


def _check_provider_key(env, import_module):
    if env.get('DELIVERY_CLOUD_EXECUTION') != '1':
        return Check('provider-key', 'warn', 'skip', 'a provider API key when execution is open',
                     'at least one configured provider key present (presence only - never a validation call)',
                     'skipped: execution is closed, no provider key is needed', 'provider key environment')
    try:
        # Providers with no key (ollama maps to None) contribute no env var; feeding
        # None into env.get crashed this whole check into the unnamed fallback
        # (2026-10-27: [warn] unknown - TypeError: str expected, not NoneType).
        names = {n for n in import_module('interview_engine.model_config')._API_KEY_ENV.values() if n}
    except Exception:
        names = {'DEEPSEEK_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'}
    present = sorted(name for name in names if env.get(name))
    if present:
        return Check('provider-key', 'warn', 'pass', 'a provider API key when execution is open',
                     'at least one configured provider key present (presence only - never a validation call)',
                     'present: %s' % ', '.join(present), 'provider key environment')
    return Check('provider-key', 'warn', 'warn', 'a provider API key when execution is open',
                 'at least one configured provider key present (presence only - never a validation call)',
                 'none of %s are set - an admitted task dies at its first provider call' % ', '.join(sorted(names)),
                 'provider key environment', 'set a provider key or close execution')


def _engine_fail(piece):
    return Check('engine-compatible', 'refuse', 'fail', 'the interview engine copy this delivery build requires',
                 'importable interview_engine.http_service (create_app, sessions DSN contract) and pg_pool.build_pool(max_size=) for pool injection',
                 piece, 'interview_engine package in this image',
                 'rebuild the image with the engine commit this delivery build requires')


def _check_engine(env, import_module):
    try:
        http = import_module('interview_engine.http_service')
    except Exception as error:
        return _engine_fail('interview_engine.http_service is not importable: %s: %s' % (type(error).__name__, str(error)[:160]))
    if not callable(getattr(http, 'create_app', None)):
        return _engine_fail('interview_engine.http_service.create_app is missing or not callable')
    if getattr(http, 'SESSIONS_DSN_ENV', None) != 'INTERVIEW_ENGINE_SESSIONS_DSN':
        return _engine_fail('the sessions DSN contract changed: SESSIONS_DSN_ENV is %r, expected INTERVIEW_ENGINE_SESSIONS_DSN'
                            % (getattr(http, 'SESSIONS_DSN_ENV', None),))
    import inspect
    try:
        pool = import_module('interview_engine.pg_pool')
        params = inspect.signature(pool.build_pool).parameters
    except Exception as error:
        return _engine_fail('interview_engine.pg_pool.build_pool is not introspectable: %s: %s' % (type(error).__name__, str(error)[:160]))
    if 'max_size' not in params:
        return _engine_fail('pg_pool.build_pool lost the max_size parameter (pool injection is impossible): %s' % list(params))
    repos = dict(pair.split('=', 1) for pair in env.get('REQUI_BUILD_REPOS', '').split(',') if '=' in pair)
    engine_sha = repos.get('interview-engine', 'development install')
    return Check('engine-compatible', 'refuse', 'pass', 'the interview engine copy this delivery build requires',
                 'importable interview_engine.http_service (create_app, sessions DSN contract) and pg_pool.build_pool(max_size=) for pool injection',
                 'interview-engine %s exposes the required surface' % engine_sha, 'interview_engine package in this image')


def _check_segments(env):
    return Check('unsettled-compute-segments', 'warn', 'skip',
                 'unsettled compute segments that block admission (the reconcile eligibility rule)',
                 'zero blocking segments per owner',
                 'not evaluable at startup: row security scopes segments to a signed-in owner - evaluated live per owner in the Debug dialog',
                 'retail_compute_segments', 'open Debug -> Startup Checks while signed in as the blocked owner')


def _check_staleness(env):
    expected = env.get('REQUI_EXPECTED_REPOS', '').strip()
    build = env.get('REQUI_BUILD_REPOS', '').strip()
    if not expected:
        return Check('build-staleness', 'warn', 'skip', 'the running image build against the build the launcher pinned',
                     'REQUI_BUILD_REPOS == REQUI_EXPECTED_REPOS',
                     'skipped: the launcher did not pin an expected build (direct docker run or server deploy)', 'REQUI_BUILD_REPOS')
    if not build or build == 'unknown':
        return Check('build-staleness', 'warn', 'warn', 'the running image build against the build the launcher pinned',
                     expected, 'the image carries no build identity', 'REQUI_BUILD_REPOS',
                     'rebuild the image from the current checkouts')
    if expected != build:
        return Check('build-staleness', 'warn', 'warn', 'the running image build against the build the launcher pinned',
                     expected, build, 'REQUI_EXPECTED_REPOS vs REQUI_BUILD_REPOS',
                     'the image is stale: rebuild it from the current checkouts')
    return Check('build-staleness', 'warn', 'pass', 'the running image build against the build the launcher pinned',
                 expected, build, 'REQUI_EXPECTED_REPOS vs REQUI_BUILD_REPOS')


def _real_connect(dsn):
    import psycopg
    return psycopg.connect(dsn, connect_timeout=10)


def run(env, *, connect=None, import_module=importlib.import_module, required_tables=REQUIRED_TABLES):
    # import_module defaults to importlib.import_module, never __import__: __import__('a.b')
    # returns the TOP package (2026-10-27: check 6 refused every real start with a bare
    # AssertionError because the package re-exports nothing, while mocked tests stayed green).
    """Run every check and return the report. Nothing here raises: an unexpected error
    inside a warn-level check becomes a warning, inside a refuse-level check a refusal."""
    connect = connect or _real_connect
    report = Report(generated_at=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))
    connection_holder = []
    builders = [('artifacts-root-writable', 'refuse', lambda: _check_artifacts(env)),
                ('database-role-claims', 'refuse', lambda: _check_database(env, connect, connection_holder)),
                ('process-log-store', 'warn', lambda: _check_process_logs(env,connect)),
                ('resource-metrics-store', 'refuse', lambda: _check_resource_metrics(env,connect)),
                ('engine-quality-store', 'refuse', lambda: _check_engine_quality(env,connect)),
                ('required-tables', 'refuse', lambda: _check_tables(env, connection_holder, required_tables)),
                ('feature-tables', 'warn', lambda: _check_feature_tables(env, connection_holder, required_tables)),
                ('pool-budget', 'warn', lambda: _check_pool_budget(env, import_module)),
                ('provider-key', 'warn', lambda: _check_provider_key(env, import_module)),
                ('engine-compatible', 'refuse', lambda: _check_engine(env, import_module)),
                ('unsettled-compute-segments', 'warn', lambda: _check_segments(env)),
                ('build-staleness', 'warn', lambda: _check_staleness(env))]
    for name, level, build in builders:
        try:
            report.checks.append(build())
        except Exception as error:
            report.checks.append(Check(name, level, 'fail' if level == 'refuse' else 'warn',
                                       'the %s preflight check' % name, 'a clean evaluation',
                                       # Exception text can contain DSNs, credentials or user data.
                                       'the check itself failed: %s (details suppressed)' % type(error).__name__,
                                       'scripts/docker/preflight.py (%s)' % name,
                                       'a preflight check must never crash - report this'))
    for connection in connection_holder:
        try: connection.close()
        except Exception: pass
    return report


def main(argv=None):
    """Standalone: python /app/preflight.py - prints the report, writes the JSON copy,
    exits 2 when a refuse-level check failed."""
    report = run(os.environ)
    print(report.render_text())
    write_report(report, report_path(os.environ))
    return 2 if report.refused else 0


if __name__ == '__main__':
    sys.exit(main())
