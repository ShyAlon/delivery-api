"""Explicit PG-first subprocess bootstrap; never accepts an external database."""
import os
from pathlib import Path
import subprocess
import sys
import pytest
import pg_harness
from pg_workspace import URL


@pytest.fixture
def pg_runtime_environment(tmp_path):
    def build(*,dsn=None):
        local=pg_harness.STATE
        if local['status']!='running':pytest.fail('disposable PG runtime unavailable: '+str(local))
        if dsn is not None and dsn!=local['dsn']:
            raise ValueError('runtime fixture requires its own disposable PG server')
        from psycopg.conninfo import conninfo_to_dict
        from urllib.parse import urlencode,quote
        fields=conninfo_to_dict(local['dsn']);database=fields.pop('dbname')
        fields['user']='delivery_app'
        role_dsn='postgresql:///'+quote(database,safe='')+'?'+urlencode(fields)
        return {**os.environ,'DELIVERY_DATABASE_URL':role_dsn,
            'DELIVERY_SUPABASE_URL':URL,'DELIVERY_SUPABASE_ANON_KEY':'hermetic-public-key',
            'TURSO_DATABASE_URL':'','TURSO_AUTH_TOKEN':'',
            'HOME':str(tmp_path/'uncreated-home'),'XDG_DATA_HOME':str(tmp_path/'uncreated-data'),
            'VMODEL_ARTIFACTS_DIR':str(tmp_path/'artifacts'),
            'PYTHONPATH':os.pathsep.join([str(Path(__file__).parents[1]/'src'),*([os.environ['DELIVERY_TEST_ENGINE_SOURCE']+'/src'] if os.getenv('DELIVERY_TEST_ENGINE_SOURCE') else []),*([os.environ['DELIVERY_TEST_SPEC_SOURCE']] if os.getenv('DELIVERY_TEST_SPEC_SOURCE') else [])])}
    return build


@pytest.fixture
def pg_runtime_process(pg_runtime_environment):
    def run(code):
        # Poison the legacy adapter before app import, while leaving PG bootstrap,
        # config validation and middleware construction real. No network auth calls.
        prefix='''
import sqlite3
from delivery_api.store import SQLiteStore,TursoReplicaStore
def forbidden(*args,**kwargs):
    raise AssertionError('PG runtime attempted a legacy SQLite/Turso open')
SQLiteStore.__init__=forbidden
TursoReplicaStore.__init__=forbidden
sqlite3.connect=forbidden
'''
        return subprocess.run([sys.executable,'-c',prefix+code],env=pg_runtime_environment(),
            capture_output=True,text=True,timeout=30)
    return run
