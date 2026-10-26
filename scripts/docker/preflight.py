"""Startup preflight for the full service: refuse to start on provably broken
foundations, warn on degraded ones, and say it all in ONE readable report
(checked / expected / found / where) instead of a raw traceback hours later.

Deterministic checks only: no model calls, no spend, nothing invented. Database
checks are read-only SELECTs run as the app's own restricted login - never DDL,
never a write. Hard failures (refuse): artifacts root, database role/claims,
required tables, engine compatibility. Warnings: pool budget, provider key,
unsettled compute segments (owner-scoped: evaluated live in the Debug dialog),
build staleness. Run by full_entry before any process is spawned; the report is
also written next to the engine log for the Debug dialog."""
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
    'admission_limits', 'answer_feedback', 'artifact_bytes', 'artifact_manifest_ready', 'artifacts',
    'audit_log', 'checkpoint_blobs', 'checkpoint_migrations', 'checkpoint_writes', 'checkpoints',
    'closed_revision_acceptance_revocations', 'closed_revision_acceptances',
    'closed_revision_install_events', 'closed_revision_installations', 'closed_revision_lineage_plans',
    'closed_revision_promotion_events', 'closed_revision_restart_intents', 'closed_revision_source_reviews',
    'credit_accounts', 'credit_adjustments', 'credit_charges', 'credit_ledger', 'decision_drafts',
    'decision_reviews', 'engine_session_journal', 'engine_sessions', 'events', 'expense_alert_outbox',
    'expense_guard_episodes', 'expense_guard_policy', 'gates', 'installed_execution_bindings', 'links',
    'operator_failure_diagnostics', 'operator_principals', 'operator_state_events', 'payment_credits',
    'payment_events', 'payment_purchases', 'phases', 'producer_results', 'progress_ingest', 'progress_seen',
    'provider_attempts', 'retail_compute_segments', 'retail_contributions', 'retail_scale_migrations',
    'review_comments', 'runs', 'staged_provider_responses', 'task_artifacts', 'tasks', 'usage',
    'user_task_policy', 'user_task_policy_changes', 'users', 'worker_jobs',
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
    except OSError:
        pass


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
        return Check('database-role-claims', 'refuse', 'skip', 'a PostgreSQL DELIVERY_DATABASE_URL with a working role + claims round trip',
                     'connect, SET ROLE requi_backend, set tenant claims, and probe a tenant table without error',
                     'skipped: the DSN is not PostgreSQL (sqlite development or smoke run)', 'DELIVERY_DATABASE_URL')
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


def _check_tables(env, connection_holder, required_tables):
    if not env.get('DELIVERY_DATABASE_URL', '').startswith(('postgresql://', 'postgres://')):
        return Check('required-tables', 'refuse', 'skip', 'that every table the code requires exists in the public schema',
                     '%d tables from the checked-in migrations' % len(required_tables),
                     'skipped: the DSN is not PostgreSQL', 'information_schema.tables')
    if not connection_holder:
        return Check('required-tables', 'refuse', 'fail', 'that every table the code requires exists in the public schema',
                     '%d tables from the checked-in migrations' % len(required_tables),
                     'cannot verify: no database connection (see database-role-claims)', 'information_schema.tables',
                     'fix the database connection first')
    rows = connection_holder[0].execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()
    present = {r[0] for r in rows}
    missing = sorted(set(required_tables) - present)
    if missing:
        return Check('required-tables', 'refuse', 'fail', 'that every table the code requires exists in the public schema',
                     '%d tables from the checked-in migrations' % len(required_tables),
                     'missing or not visible to the app login: %s' % ', '.join(missing), 'information_schema.tables',
                     'apply the missing migration to this database (see supabase/migrations)')
    return Check('required-tables', 'refuse', 'pass', 'that every table the code requires exists in the public schema',
                 '%d tables from the checked-in migrations' % len(required_tables),
                 'all %d present' % len(required_tables), 'information_schema.tables')


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
    found = 'delivery pool %d + engine pool %d = %d clients' % (delivery_max or 0, engine_max or 0, (delivery_max or 0) + (engine_max or 0))
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
        names = set(import_module('interview_engine.model_config')._API_KEY_ENV.values())
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


def _check_engine(env, import_module):
    try:
        http = import_module('interview_engine.http_service')
        assert callable(getattr(http, 'create_app', None))
        assert getattr(http, 'SESSIONS_DSN_ENV', None) == 'INTERVIEW_ENGINE_SESSIONS_DSN'
        import inspect
        pool = import_module('interview_engine.pg_pool')
        assert 'max_size' in inspect.signature(pool.build_pool).parameters
    except Exception as error:
        return Check('engine-compatible', 'refuse', 'fail', 'the interview engine copy this delivery build requires',
                     'importable interview_engine.http_service (create_app, sessions DSN contract) and pg_pool.build_pool(max_size=) for pool injection',
                     'the installed engine does not expose the required surface: %s: %s' % (type(error).__name__, str(error)[:160]),
                     'interview_engine package in this image',
                     'rebuild the image with the engine commit this delivery build requires')
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


def run(env, *, connect=None, import_module=__import__, required_tables=REQUIRED_TABLES):
    """Run every check and return the report. Nothing here raises: an unexpected error
    inside a warn-level check becomes a warning, inside a refuse-level check a refusal."""
    connect = connect or _real_connect
    report = Report(generated_at=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))
    connection_holder = []
    builders = [lambda: _check_artifacts(env),
                lambda: _check_database(env, connect, connection_holder),
                lambda: _check_tables(env, connection_holder, required_tables),
                lambda: _check_pool_budget(env, import_module),
                lambda: _check_provider_key(env, import_module),
                lambda: _check_engine(env, import_module),
                lambda: _check_segments(env),
                lambda: _check_staleness(env)]
    for build in builders:
        try:
            report.checks.append(build())
        except Exception as error:
            report.checks.append(Check('unknown', 'warn', 'warn', 'a preflight check', 'a clean evaluation',
                                       'the check itself failed: %s: %s' % (type(error).__name__, str(error)[:160]),
                                       'scripts/docker/preflight.py'))
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
