import importlib.util
from pathlib import Path
import pytest
ROOT=Path(__file__).parents[1]
SPEC=importlib.util.spec_from_file_location('schema_readiness',ROOT/'scripts/ops/schema-readiness.py')
m=importlib.util.module_from_spec(SPEC)

def load():SPEC.loader.exec_module(m);return m

def test_all_rollout_files_have_catalog_probes():
    s=load();assert len(s.MIGRATIONS)==16
    assert list(s.MIGRATIONS)==sorted(s.MIGRATIONS)
    assert '20261030030000_resource_metrics_maintenance.sql' in s.MIGRATIONS

def test_destructive_body_and_dynamic_sql_are_refused_not_grant_keyword():
    s=load()
    assert s.destructive('GRANT SELECT,DELETE ON t TO writer;')==[]
    assert s.destructive('CREATE FUNCTION f() RETURNS void AS $$ BEGIN DELETE FROM t; END $$ LANGUAGE plpgsql;')==['DELETE']
    assert s.destructive("DO $$ BEGIN EXECUTE 'DROP TABLE t'; END $$;")==['DROP']
    assert s.destructive('-- DELETE FROM x\nCREATE TABLE t(x text);')==[]

def test_checked_in_retention_migration_is_blocked():
    s=load();path=ROOT/'supabase/migrations/20261030030000_resource_metrics_maintenance.sql'
    assert s.destructive(path.read_text())==['DELETE']

def test_preview_checks_live_schema_before_grounding_and_build():
    src=(ROOT/'scripts/ops/preview-exec.sh').read_text()
    assert src.index('schema-readiness.py')<src.index('step "settings + grounding"')
    assert src.index('schema-readiness.py')<src.index('step "build ')

def owner():
    spec=importlib.util.spec_from_file_location('owner_schema',ROOT/'scripts/owner/apply-pg-cutover.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_apply_refuses_destructive_or_partial_before_any_sql():
    s=owner()
    class NoWrites:
        def execute(self,*a):raise AssertionError('no SQL allowed')
    with pytest.raises(ValueError,match='destructive'):s.apply(NoWrites(),[{'file':'maintenance.sql','status':'pending','destructive':['DELETE']}],None,ROOT,'delivery_app',lambda _:'yes')
    with pytest.raises(ValueError,match='partial'):s.apply(NoWrites(),[{'file':'partial.sql','status':'partial'}],None,ROOT,'delivery_app',lambda _:'yes')

def test_apply_commits_one_file_and_rolls_back_first_failure(tmp_path,monkeypatch):
    import psycopg
    s=owner();folder=tmp_path/'supabase/migrations';folder.mkdir(parents=True)
    (folder/'a.sql').write_text('CREATE TABLE first(x int);')
    (folder/'b.sql').write_text('INVALID SQL;')
    rows=[{'file':name,'status':'pending','destructive':[]} for name in ('a.sql','b.sql')]
    monkeypatch.setattr(s,'plan',lambda *args:rows)
    calls=[]
    class Tx:
        def __enter__(self):calls.append('BEGIN')
        def __exit__(self,t,*args):calls.append('ROLLBACK' if t else 'COMMIT')
    class Conn:
        def transaction(self):return Tx()
        def execute(self,sql):
            calls.append(sql)
            if sql=='INVALID SQL;':raise psycopg.errors.SyntaxError('bad SQL')
    assert s.apply(Conn(),rows,None,tmp_path,'delivery_app',lambda _:'APPLY 2 MIGRATIONS TO delivery_app')==1
    assert calls.count('COMMIT')==1 and calls.count('ROLLBACK')==1
    assert not any('GRANT' in str(x) for x in calls)

def test_inventory_on_real_owned_pg_is_ready_and_app_grants_available(pg_runtime_environment):
    import psycopg
    s=load();env=pg_runtime_environment()
    with psycopg.connect(env['DELIVERY_DATABASE_URL'],autocommit=True) as conn:
        conn.execute('SET default_transaction_read_only=on')
        assert all(row['status']=='present' for row in s.inventory(conn))
        assert s.grants(conn)==[]

def test_retention_exception_is_exact_filename_and_hash():
    import hashlib
    s=owner();name='20261030030000_resource_metrics_maintenance.sql'
    digest=hashlib.sha256((ROOT/'supabase/migrations'/name).read_bytes()).hexdigest()
    assert s.APPROVED_DESTRUCTIVE=={name:digest}
    assert s.APPROVED_DESTRUCTIVE.get('renamed.sql')!=digest
    assert s.APPROVED_DESTRUCTIVE[name]!=hashlib.sha256(b'changed DELETE FROM other;').hexdigest()
    text=(ROOT/'supabase/migrations'/name).read_text()
    assert 'cron.schedule' not in text and 'SELECT public.requi_maintain_resource_metrics(' not in text

def test_apply_seven_real_migrations_to_owned_disposable_db():
    import importlib,os,tempfile,shutil
    import pgserver,psycopg
    import pg_harness
    s=owner();r=load();scratch=tempfile.mkdtemp(prefix='schema-rollout-test-');server=pgserver.get_server(scratch,cleanup_mode='delete')
    try:
        with psycopg.connect(server.get_uri(),autocommit=True) as conn:
            conn.execute(pg_harness.ROLES)
            conn.execute(pg_harness.SUPABASE_STAND_IN)
            conn.execute(pg_harness.SUPABASE_DEFAULT_ACL)
            for path in sorted((ROOT/'supabase/migrations').glob('*.sql')):
                if path.name<'20261029203000_resource_metrics.sql' and path.name[:14].isdigit():conn.execute(path.read_text())
            rows=s.plan(conn,r,ROOT)
            assert len([x for x in rows if x['status']=='pending'])==16
            # New file has DELETE only inside private projection functions, but
            # does not inherit the owner's exact old retention-file grant.
            with pytest.raises(ValueError,match='destructive'):s.apply(conn,rows,r,ROOT,'delivery_app',lambda _:'APPLY 16 MIGRATIONS TO delivery_app')
            conn.execute((ROOT/'supabase/migrations/20261031163000_admission_totals.sql').read_text())
            conn.execute((ROOT/'supabase/migrations/20261031190000_retail_totals.sql').read_text())
            rows=s.plan(conn,r,ROOT)
            # Apply dependencies only in this owned disposable fixture, then the
            # new cache file directly; the production helper stays refusal-first.
            with pytest.raises(ValueError,match='destructive'):s.apply(conn,rows,r,ROOT,'delivery_app',lambda _:'APPLY 15 MIGRATIONS TO delivery_app')
            for row in rows:
                if row['status']=='pending':conn.execute((ROOT/'supabase/migrations'/row['file']).read_text())
            rows=s.plan(conn,r,ROOT)
            assert s.apply(conn,rows,r,ROOT,'delivery_app',lambda _:'APPLY 0 MIGRATIONS TO delivery_app')==0
            assert all(x['status']=='present' for x in r.inventory(conn))
            for role in r.ROLES:assert conn.execute("SELECT pg_has_role('delivery_app',%s,'SET')",(role,)).fetchone()[0]
    finally:server.cleanup();shutil.rmtree(scratch,ignore_errors=True)


def test_normal_apply_includes_usage_role_and_standard_settings_save():
    r=load()
    assert 'requi_operator_usage' in r.ROLES
    text=(ROOT/'scripts/owner/apply-pg-cutover.py').read_text()
    assert 'operator_setup.run(root,explicit=args.env_file)' in text
    assert text.index('result=apply(')<text.index('operator_setup.run(')


def test_every_checked_in_migration_has_explicit_inventory_coverage():
    s=load()
    disk={p.name for p in (ROOT/'supabase/migrations').glob('*.sql')}
    baseline=set(getattr(s,'BASELINE_MIGRATIONS',()))
    assert not baseline & set(s.MIGRATIONS)
    assert baseline | set(s.MIGRATIONS)==disk


def test_own_test_guard_requires_table_and_exact_function_signature():
    s=load()
    assert s.MIGRATIONS['20261030223000_own_test_expense_profile.sql']==(
        'table:own_test_expense_profiles','signature:requi_own_test_expense_guard(text,bigint)')


def test_unlisted_new_migration_refuses_before_any_database_probe(tmp_path,monkeypatch):
    s=load();folder=tmp_path/'supabase/migrations';folder.mkdir(parents=True)
    for name in s.BASELINE_MIGRATIONS | set(s.MIGRATIONS):(folder/name).write_text('-- fixture')
    s.validate_coverage(tmp_path)
    (folder/'20261101000000_new.sql').write_text('CREATE TABLE future(x int);')
    with pytest.raises(ValueError,match='20261101000000_new.sql'):s.validate_coverage(tmp_path)


def test_signature_probe_passes_exact_types_and_reports_partial(monkeypatch):
    s=load();calls=[]
    class Conn:
        def execute(self,sql,args):
            calls.append(args[0]);return self
        def fetchone(self):return (False,)
    assert not s.object_exists(Conn(),'signature:requi_own_test_expense_guard(text,bigint)')
    assert calls==['public.requi_own_test_expense_guard(text,bigint)']


def test_own_test_table_without_guard_function_is_partial(monkeypatch):
    s=load()
    monkeypatch.setattr(s,'object_exists',lambda conn,probe:not probe.startswith('signature:'))
    rows=s.inventory(None)
    own=next(r for r in rows if r['file']=='20261030223000_own_test_expense_profile.sql')
    assert own['status']=='partial' and own['destructive']==[]
