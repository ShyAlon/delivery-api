#!/usr/bin/env python3
"""Initialize an empty disposable local DB. No production connection or cleanup."""
import argparse,re
from pathlib import Path
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg import sql


def bootstrap(dsn,migrations):
    info=conninfo_to_dict(dsn)
    if info.get('host') not in ('127.0.0.1','localhost','::1','/tmp') or not re.fullmatch(r'requi_item6_[a-z0-9_]{8,60}',info.get('dbname','')):
        raise ValueError('explicit disposable local database required')
    with psycopg.connect(dsn) as c:
        if c.execute("SELECT 1 FROM information_schema.tables WHERE table_schema IN ('public','auth') LIMIT 1").fetchone():
            raise ValueError('empty database required; never migrate, retry or overwrite existing run')
        c.execute("""DO $$ BEGIN
            IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
                CREATE ROLE authenticated NOLOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
            IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='delivery_app') THEN
                CREATE ROLE delivery_app LOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
            IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='delivery_payment_test') THEN
                CREATE ROLE delivery_payment_test LOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
        END $$""")
        c.execute('GRANT authenticated TO delivery_app')
        c.execute('CREATE SCHEMA auth')
        c.execute('CREATE TABLE auth.users(id uuid PRIMARY KEY)')
        c.execute('CREATE TABLE auth.sessions(id uuid PRIMARY KEY,user_id uuid REFERENCES auth.users(id))')
        c.execute("CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('request.jwt.claim.sub',true),'')::uuid; $$")
        c.execute('GRANT USAGE ON SCHEMA auth TO authenticated')
        c.execute('GRANT EXECUTE ON FUNCTION auth.uid() TO authenticated')
        paths=sorted(Path(migrations).glob('*.sql'))
        if not paths:raise ValueError('exact source migrations required')
        for path in paths:c.execute(path.read_text(),prepare=False)
        c.execute('GRANT requi_payment TO delivery_payment_test')
        c.execute('GRANT requi_operator_payment_read TO delivery_app')
    return {'database_name':info['dbname'],'migration_count':len(paths),'empty_test_db_initialized':True,'launch_authorized':False}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dsn',required=True);p.add_argument('--migrations',required=True,type=Path)
    a=p.parse_args();print(bootstrap(a.dsn,a.migrations))
