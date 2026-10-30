import importlib.util
import io
import json
import os
from pathlib import Path
from uuid import uuid4
import pytest
import psycopg

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('operator_setup',ROOT/'scripts/ops/operator-config-setup.py')
setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)


def test_refuses_zero_multiple_invalid_or_incomplete_rows():
    row=(str(uuid4()),'ops-v1',sorted(setup.check.DEBUG_CAPABILITIES))
    for rows in ([],[row,row],[('bad','ops-v1',row[2])],[(row[0],'',row[2])],[(row[0],'v1',['logs.read'])]):
        with pytest.raises(ValueError):setup.from_rows(rows)
    result=setup.from_rows([row])
    assert result['REQUI_OPERATOR_SUBJECT']==row[0]
    assert set(json.loads(result['REQUI_OPERATOR_CAPABILITIES']))==setup.check.DEBUG_CAPABILITIES


def test_private_atomic_save_confirms_preserves_values_and_refuses_drift(tmp_path):
    s=setup.settings_module()
    dest=tmp_path/'preview.env';dest.write_text('OTHER=keep\nDELIVERY_DATABASE_URL=private\n');dest.chmod(0o600)
    cfg=setup.from_rows([(str(uuid4()),'private-version',sorted(setup.check.DEBUG_CAPABILITIES))])
    before=dest.read_bytes()
    assert setup.save(s,dest,cfg,lambda prompt:'no') is False
    assert dest.read_bytes()==before
    assert setup.save(s,dest,cfg,lambda prompt:'yes') is True
    assert dest.stat().st_mode & 0o777==0o600
    result=s.read_file(dest)
    assert result['OTHER']=='keep' and result['DELIVERY_DATABASE_URL']=='private'
    assert all(result[k]==v for k,v in cfg.items())
    def drift(prompt):
        dest.write_text('OTHER=changed\n');dest.chmod(0o600);return 'yes'
    with pytest.raises(ValueError):setup.save(s,dest,cfg,drift)
    assert dest.read_text()=='OTHER=changed\n'


def test_real_registry_read_only_and_hidden_refusal():
    dsn=os.environ['DELIVERY_TEST_POSTGRES_DSN'];subject=str(uuid4())
    with psycopg.connect(dsn) as c:
        c.execute('DELETE FROM operator_principals')
        c.execute('INSERT INTO auth.users(id) VALUES(%s)',(subject,))
        c.execute('INSERT INTO operator_principals VALUES(%s,%s,%s,true)',(subject,'ops-v1',sorted(setup.check.DEBUG_CAPABILITIES)))
    try:
        with setup.check.read_connection(dsn) as c:
            cfg=setup.read_registry(c)
            assert cfg['REQUI_OPERATOR_SUBJECT']==subject
            assert c.execute('SHOW transaction_read_only').fetchone()[0]=='on'
        with setup.check.read_connection(dsn.replace('user=sandbox','user=delivery_app')) as c:
            with pytest.raises(ValueError):setup.read_registry(c)
    finally:
        with psycopg.connect(dsn) as c:
            c.execute('DELETE FROM operator_principals WHERE subject=%s',(subject,))
            c.execute('DELETE FROM public.users WHERE user_id=%s',(subject,))
            c.execute('DELETE FROM auth.users WHERE id=%s',(subject,))


def test_targets_refuse_different_database():
    assert setup.same_target('postgresql://a:p@host/db','postgresql://b:q@host/db')
    assert not setup.same_target('postgresql://a:p@host/db','postgresql://b:q@other/db')


def test_failure_outputs_only_fixed_booleans_no_save(tmp_path):
    out=io.StringIO()
    assert setup.run(tmp_path,env={},out=out)==1
    assert not (tmp_path/'preview.env').exists()
    assert all(line.split('=')[-1] in ('true','false') for line in out.getvalue().splitlines())


def test_full_setup_saves_after_confirm_and_outputs_no_values(tmp_path):
    from contextlib import contextmanager
    from unittest.mock import patch
    dsn=os.environ['DELIVERY_TEST_POSTGRES_DSN']
    row=(str(uuid4()),'private-registry-version',sorted(setup.check.DEBUG_CAPABILITIES|{'credits.allocate'}))
    cfg={'DELIVERY_DATABASE_URL':'postgresql://delivery_app.project:private@pooler.invalid/db',
         'REQUI_ADMIN_DATABASE_URL':'postgresql://admin.project:secret@pooler.invalid/db',
         'XDG_CONFIG_HOME':str(tmp_path)}
    @contextmanager
    def connect(target):
        assert target==cfg['REQUI_ADMIN_DATABASE_URL']
        with setup.check.read_connection(dsn) as conn:yield conn
    out=io.StringIO();prompts=[]
    def confirm(prompt):prompts.append(prompt);return 'yes'
    with patch.object(setup,'read_registry',lambda conn:setup.from_rows([row])):
        assert setup.run(tmp_path,env=cfg,connect=connect,confirm=confirm,out=out)==0
    saved=setup.settings_module().read_file(tmp_path/'requi/preview.env')
    assert saved['REQUI_OPERATOR_SUBJECT']==row[0]
    assert 'credits.allocate' not in saved['REQUI_OPERATOR_CAPABILITIES']
    assert set(saved)==set(setup.check.SETTINGS)
    text=out.getvalue()+' '.join(prompts)
    for value in (row[0],row[1],*cfg.values()):assert value not in text
    assert 'settings.saved=true' in text


def test_symlink_target_refused(tmp_path):
    target=tmp_path/'other';target.write_text('OTHER=keep\n');target.chmod(0o600)
    link=tmp_path/'preview.env';link.symlink_to(target)
    with pytest.raises(ValueError):setup.save(setup.settings_module(),link,{},lambda prompt:'yes')
    assert target.read_text()=='OTHER=keep\n'
