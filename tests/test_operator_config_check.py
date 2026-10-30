"""Only the owned disposable PG is used; never operator/live settings."""
import importlib.util
import io
import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('operator_config_check',ROOT/'scripts/ops/operator-config-check.py')
check=importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def values(subject):
    return {'REQUI_OPERATOR_SUBJECT':subject,'REQUI_OPERATOR_REGISTRY_VERSION':'private-version',
            'REQUI_OPERATOR_CAPABILITIES':json.dumps(sorted(check.DEBUG_CAPABILITIES))}


def test_individual_presence_and_validation():
    report=check.configuration({})
    for name in check.SETTINGS:
        assert report[name+'.present'] is False
        assert report[name+'.valid'] is False
    good=values(str(uuid4()))
    assert all(check.configuration(good).values())
    for name,bad in [('REQUI_OPERATOR_SUBJECT','bad'),('REQUI_OPERATOR_REGISTRY_VERSION',''),
                     ('REQUI_OPERATOR_CAPABILITIES','["logs.read","logs.read"]'),
                     ('REQUI_OPERATOR_CAPABILITIES','["unknown"]')]:
        assert check.configuration({**good,name:bad})[name+'.valid'] is False
    assert check.configuration({**good,'REQUI_OPERATOR_CAPABILITIES':'["logs.read"]'})['debug_capabilities.configured'] is False


def test_real_registry_missing_match_mismatch_and_read_only():
    dsn=os.environ['DELIVERY_TEST_POSTGRES_DSN']
    subject=str(uuid4());cfg=values(subject)
    with psycopg.connect(dsn) as c:
        c.execute('DELETE FROM operator_principals')
        c.execute('INSERT INTO auth.users(id) VALUES(%s)',(subject,))
    try:
        with check.read_connection(dsn) as c:
            assert c.execute('SHOW transaction_read_only').fetchone()[0]=='on'
            absent=check.database(c,cfg,'delivery_app')
            assert absent['registry.inspected'] is True
            assert absent['registry.enabled_row_present'] is False
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                c.execute('INSERT INTO auth.users(id) VALUES(%s)',(str(uuid4()),))
        with psycopg.connect(dsn) as c:
            c.execute('INSERT INTO operator_principals VALUES(%s,%s,%s,true)',(subject,cfg['REQUI_OPERATOR_REGISTRY_VERSION'],sorted(check.DEBUG_CAPABILITIES)))
        with check.read_connection(dsn) as c:
            good=check.database(c,cfg,'delivery_app')
            assert all(good.values())
        private_cfg={**cfg,'DELIVERY_DATABASE_URL':dsn.replace('user=sandbox','user=delivery_app'),'REQUI_ADMIN_DATABASE_URL':dsn}
        from unittest.mock import patch
        # Full loader -> admin read-only connection -> boolean report, app role checked separately.
        with patch.object(check,'configuration',wraps=check.configuration):
            out=io.StringIO()
            assert check.run(ROOT,env=private_cfg,out=out)==0
            assert 'configuration.ready=true' in out.getvalue()
            assert subject not in out.getvalue() and 'private-version' not in out.getvalue()
        with check.read_connection(dsn) as c:
            mismatch=check.database(c,{**cfg,'REQUI_OPERATOR_SUBJECT':str(uuid4()),'REQUI_OPERATOR_REGISTRY_VERSION':'wrong'},'delivery_app')
            assert mismatch['registry.subject_matches'] is False
            assert mismatch['registry.version_matches'] is False
        with psycopg.connect(dsn) as c:
            c.execute("UPDATE operator_principals SET capabilities=ARRAY['logs.read']")
        with check.read_connection(dsn) as c:
            assert check.database(c,cfg,'delivery_app')['registry.debug_capabilities_present'] is False
        # RLS-hidden is NOT a confirmed missing row.
        with check.read_connection(dsn.replace('user=sandbox','user=delivery_app')) as c:
            denied=check.database(c,cfg,'delivery_app')
            assert denied['registry.inspected'] is False
            assert 'registry.enabled_row_present' not in denied
    finally:
        with psycopg.connect(dsn) as c:
            c.execute('DELETE FROM operator_principals WHERE subject=%s',(subject,))
            c.execute('DELETE FROM public.users WHERE user_id=%s',(subject,))
            c.execute('DELETE FROM auth.users WHERE id=%s',(subject,))


def test_connection_errors_and_settings_values_never_print(tmp_path):
    root=tmp_path/'repo';root.mkdir()
    private=tmp_path/'private.env'
    cfg=values(str(uuid4()))
    cfg.update(REQUI_ADMIN_DATABASE_URL='postgresql://private-user:private-password@private-host/private-db',
               DELIVERY_DATABASE_URL='postgresql://private-app:secret@private-host/private-db')
    private.write_text('\n'.join(k+'='+v for k,v in cfg.items()));private.chmod(0o600)
    def fail(*args,**kwargs):raise RuntimeError(str(cfg))
    out=io.StringIO()
    assert check.run(root,env={},explicit=str(private),connect=fail,out=out)==1
    text=out.getvalue()
    assert 'database.connected=false' in text and 'registry.inspected=false' in text
    assert all(line.split('=')[-1] in ('true','false') for line in text.splitlines())
    for secret in cfg.values():assert secret not in text


def test_cli_path_and_private_file_first(tmp_path):
    root=tmp_path/'repo';root.mkdir()
    (root/'.env').write_text('REQUI_OPERATOR_REGISTRY_VERSION=from-file\n')
    (root/'.env').chmod(0o600)
    out=io.StringIO()
    assert check.run(root,env={},out=out)==1
    assert 'REQUI_OPERATOR_REGISTRY_VERSION.present=true' in out.getvalue()
    assert 'REQUI_OPERATOR_SUBJECT.present=false' in out.getvalue()
    assert 'registry.inspected=false' in out.getvalue()


def test_confirmed_missing_table_is_not_inability_to_inspect():
    class Result:
        def fetchone(self):return (False,)
    class Missing:
        def execute(self,sql,args=None):
            assert sql.startswith('SELECT ')
            return Result()
    result=check.database(Missing(),{},None)
    assert result['registry.table_present'] is False
    assert result['registry.inspected'] is False
    assert 'registry.enabled_row_present' not in result


def test_query_failure_never_becomes_confirmed_missing(tmp_path):
    from contextlib import contextmanager
    private=tmp_path/'config.env'
    private.write_text('DELIVERY_DATABASE_URL=postgresql://private-app:secret@private-host/db\n')
    private.chmod(0o600)
    class Denied:
        def execute(self,*args,**kwargs):raise RuntimeError('private database detail')
    @contextmanager
    def connect(dsn):yield Denied()
    out=io.StringIO()
    assert check.run(tmp_path,env={},explicit=str(private),connect=connect,out=out)==1
    text=out.getvalue()
    assert 'database.connected=true' in text
    assert 'registry.inspected=false' in text
    assert 'registry.enabled_row_present' not in text
    assert 'check.completed=false' in text
    assert 'private' not in text and 'secret' not in text


def test_invalid_private_file_is_boolean_only(tmp_path):
    private=tmp_path/'config.env';private.write_text('SECRET=do-not-print')
    private.chmod(0o644)
    out=io.StringIO()
    assert check.run(tmp_path,env={},explicit=str(private),out=out)==1
    assert 'settings.loaded=false' in out.getvalue()
    assert 'do-not-print' not in out.getvalue()


def test_cli_accepts_repository_path(tmp_path):
    import subprocess
    # No inherited settings/DSN may reach the child. This exercises only absence.
    env={'PATH':os.environ['PATH'],'HOME':str(tmp_path),'XDG_CONFIG_HOME':str(tmp_path)}
    proc=subprocess.run([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/ops/operator-config-check.py'),str(tmp_path)],env=env,capture_output=True,text=True)
    assert proc.returncode==1 and not proc.stderr
    assert 'REQUI_OPERATOR_SUBJECT.present=false' in proc.stdout
    assert 'configuration.ready=false' in proc.stdout


def test_pooler_username_is_not_used_as_postgres_role(tmp_path):
    from contextlib import contextmanager
    dsn=os.environ['DELIVERY_TEST_POSTGRES_DSN']
    cfg={'DELIVERY_DATABASE_URL':'postgresql://delivery_app.project:private@pooler.invalid/db',
         'REQUI_ADMIN_DATABASE_URL':dsn}
    @contextmanager
    def connect(target):
        actual=dsn.replace('user=sandbox','user=delivery_app') if target==cfg['DELIVERY_DATABASE_URL'] else target
        with check.read_connection(actual) as conn:yield conn
    out=io.StringIO()
    check.run(tmp_path,env=cfg,connect=connect,out=out)
    assert 'app_role.can_set_operator_read=true' in out.getvalue()
