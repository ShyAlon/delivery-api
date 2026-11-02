#!/usr/bin/env python3
"""Boolean-only operator configuration preflight. No grants or settings writes.

Usage: .venv/bin/python scripts/ops/operator-config-check.py REPO [--env-file PATH]
Uses the existing private file-first settings loader. An existing admin DSN can
inspect the registry; restricted/RLS-hidden results are never called missing.
This is configuration evidence, not a signed-in HTTP authorization smoke test.
"""
import argparse
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import sys
from uuid import UUID

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from delivery_api.operator_authorization import CAPABILITIES
from delivery_api.operator_startup import SETTINGS
from delivery_api.owner_diagnostics import failure,OWNER_CONNECT_TIMEOUT_SECONDS

DEBUG_CAPABILITIES=frozenset({'logs.read','debug.read','sessions.read','analytics.read','usage.read'})


def capabilities(value):
    try:
        result=json.loads(value)
        if not isinstance(result,list) or not result or any(not isinstance(c,str) for c in result):return None
        if len(set(result))!=len(result) or not set(result)<=CAPABILITIES:return None
        return frozenset(result)
    except (TypeError,ValueError):return None


def configuration(values):
    result={}
    for name in SETTINGS:
        value=values.get(name)
        result[name+'.present']=bool(value)
        if name=='REQUI_OPERATOR_SUBJECT':
            try:valid=str(UUID(value))==value
            except (ValueError,TypeError,AttributeError):valid=False
        elif name=='REQUI_OPERATOR_CAPABILITIES':valid=capabilities(value) is not None
        else:valid=isinstance(value,str) and bool(value)
        result[name+'.valid']=valid
    configured=capabilities(values.get(SETTINGS[2])) or frozenset()
    for cap in sorted(DEBUG_CAPABILITIES):result['configured.'+cap+'.present']=cap in configured
    result['debug_capabilities.configured']=DEBUG_CAPABILITIES <= configured
    return result


@contextmanager
def read_connection(dsn):
    stage='driver.import'
    try:
        import psycopg
        stage='database.connect_readonly_startup'
        with psycopg.connect(dsn,connect_timeout=OWNER_CONNECT_TIMEOUT_SECONDS,options='-c default_transaction_read_only=on') as conn:
            stage='database.begin_readonly'
            with conn.transaction():
                conn.execute('SET TRANSACTION READ ONLY')
                stage='database.statement_timeout'
                conn.execute("SET LOCAL statement_timeout='5s'")
                stage='database.read_queries'
                yield conn
    except Exception as exc:
        exc.owner_diagnostic_stage=stage
        raise


def database(conn,values,app_user):
    result={'registry.inspected':False}
    exists=conn.execute("SELECT to_regclass('public.operator_principals') IS NOT NULL").fetchone()[0]
    result['registry.table_present']=bool(exists)
    if exists:
        # FORCE RLS with no SELECT policy can return no rows even to a table
        # owner. Refuse to call that a missing operator; no bypass is attempted.
        visible=conn.execute("SELECT has_table_privilege(current_user,'public.operator_principals','SELECT') AND NOT row_security_active('public.operator_principals'::regclass)").fetchone()[0]
        if visible:
            rows=conn.execute('SELECT subject,registry_version,capabilities FROM public.operator_principals WHERE enabled LIMIT 2').fetchall()
            result['registry.inspected']=True
            result['registry.enabled_row_present']=bool(rows)
            result['registry.exactly_one_enabled_row']=len(rows)==1
            if len(rows)==1:
                subject,version,caps=rows[0]
                result['registry.subject_matches']=str(subject)==values.get(SETTINGS[0])
                result['registry.version_matches']=version==values.get(SETTINGS[1])
                result['registry.debug_capabilities_present']=DEBUG_CAPABILITIES<=set(caps)
                for cap in sorted(DEBUG_CAPABILITIES):result['registry.'+cap+'.present']=cap in caps
                configured=capabilities(values.get(SETTINGS[2]))
                result['registry.configured_capabilities_allowed']=configured is not None and configured<=set(caps)
    # Check the actual app DSN's login, not the inspecting admin login.
    result['app_role.inspected']=False
    if app_user:
        role=conn.execute("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=%s),EXISTS(SELECT 1 FROM pg_roles WHERE rolname='requi_operator_read')",(app_user,)).fetchone()
        result['app_role.inspected']=True
        result['app_role.can_set_operator_read']=bool(all(role) and conn.execute("SELECT pg_has_role(%s,'requi_operator_read','SET')",(app_user,)).fetchone()[0])
    return result


def run(root,*,env=None,explicit=None,connect=read_connection,out=None):
    out=sys.stdout if out is None else out
    report={'settings.loaded':False,'database.connected':False,'registry.inspected':False}
    stage='settings.load'
    try:
        spec=importlib.util.spec_from_file_location('requi_operator_settings',ROOT/'scripts/ops/settings.py')
        settings=importlib.util.module_from_spec(spec);spec.loader.exec_module(settings)
        values=settings.load(Path(root),env=env,explicit=explicit)
        report['settings.loaded']=True
        report.update(configuration(values))
        report['DELIVERY_DATABASE_URL.present']=bool(values.get('DELIVERY_DATABASE_URL'))
        report['REQUI_ADMIN_DATABASE_URL.present']=bool(values.get('REQUI_ADMIN_DATABASE_URL'))
        dsn=values.get('REQUI_ADMIN_DATABASE_URL') or values.get('DELIVERY_DATABASE_URL')
        if dsn:
            stage='registry.read_check'
            with connect(dsn) as conn:
                report['database.connected']=True
                report.update(database(conn,values,None))
        # Pooler URI usernames may contain a project suffix; only the live
        # app connection knows its actual PostgreSQL identity and privileges.
        if values.get('DELIVERY_DATABASE_URL'):
            try:
                stage='app_role.read_check'
                with connect(values['DELIVERY_DATABASE_URL']) as app:
                    for role,key in (('requi_operator_read','app_role.can_set_operator_read'),('requi_operator_usage','app_role.can_set_operator_usage')):
                        exists=app.execute('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=%s)',(role,)).fetchone()[0]
                        report[key]=bool(exists and app.execute("SELECT pg_has_role(current_user,%s,'SET')",(role,)).fetchone()[0])
                    report['app_role.inspected']=True
            except Exception as exc:
                failure(report,exc,stage)
                report['app_role.inspected']=False
                report.pop('app_role.can_set_operator_read',None)
                report.pop('app_role.can_set_operator_usage',None)
    except Exception as exc:
        failure(report,exc,stage)
        # Never include exception text, DSN, private file contents or identity.
        # A failed query means unverified, not a confirmed absent registry row.
        report['check.completed']=False
    else:report['check.completed']=True
    required=('settings.loaded','check.completed','database.connected','registry.inspected',
              'registry.table_present','registry.enabled_row_present','registry.exactly_one_enabled_row',
              'registry.subject_matches','registry.version_matches','registry.debug_capabilities_present',
              'registry.configured_capabilities_allowed','app_role.inspected','app_role.can_set_operator_read',
              'app_role.can_set_operator_usage','DELIVERY_DATABASE_URL.present','debug_capabilities.configured')
    report['configuration.ready']=all(report.get(k,False) for k in required) and all(report.get(n+'.valid',False) for n in SETTINGS)
    for key,value in report.items():print(key+'='+str(bool(value)).lower(),file=out)
    return 0 if report['configuration.ready'] else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo',type=Path)
    parser.add_argument('--env-file')
    args=parser.parse_args()
    return run(args.repo,explicit=args.env_file)


if __name__=='__main__':sys.exit(main())
