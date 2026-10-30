"""Run a smoke command with this process's disposable migrated PG, never a caller DSN.

Usage: python owned-pg-smoke.py REPO_ROOT COMMAND [ARG ...]
The supervisor needs pgserver/psycopg; COMMAND may use a clean installed-wheel venv.
"""
import os,sys
from pathlib import Path
import subprocess
from urllib.parse import quote,urlencode


def main():
    if any(os.environ.get(k) for k in ('DELIVERY_TEST_POSTGRES_DSN','DELIVERY_TEST_POSTGRES_URL','DELIVERY_DATABASE_URL')):
        raise ValueError('refuse inherited database configuration; smoke owns its database')
    root=Path(sys.argv[1]).resolve()
    sys.path.insert(0,str(root/'tests'))
    import pg_harness
    pg_harness.start()
    if pg_harness.STATE['status']!='running':
        raise RuntimeError('disposable PG failed: '+str(pg_harness.STATE))
    from psycopg.conninfo import conninfo_to_dict
    fields=conninfo_to_dict(pg_harness.STATE['dsn']);database=fields.pop('dbname');fields['user']='delivery_app'
    dsn='postgresql:///'+quote(database,safe='')+'?'+urlencode(fields)
    env=dict(os.environ)
    env.update(DELIVERY_TEST_POSTGRES_URL=dsn,DELIVERY_DATABASE_URL=dsn,
        DELIVERY_SUPABASE_URL='https://isolated-smoke.supabase.co',DELIVERY_SUPABASE_ANON_KEY='hermetic-public-key')
    return subprocess.run(sys.argv[2:],env=env,check=False).returncode


if __name__=='__main__':sys.exit(main())
