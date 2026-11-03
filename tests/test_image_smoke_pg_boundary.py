"""The actual build/reuse entry points must own disposable PG, not assume a DSN."""
from pathlib import Path
import os,subprocess
import pytest
ROOT=Path(__file__).parents[1]

def test_every_full_image_smoke_caller_uses_shared_owned_pg_gate():
    for name in ('scripts/docker/build-local-image.sh','scripts/docker/smoke-image.sh','scripts/ops/preview.sh','scripts/ops/preview-exec.sh'):
        text=(ROOT/name).read_text()
        assert 'smoke-local-image.sh' in text,name
        assert 'python /app/full-smoke.py' not in text,name

def test_shared_gate_builds_test_derivative_and_runs_network_none(tmp_path):
    fake=tmp_path/'bin';fake.mkdir();log=tmp_path/'log'
    (fake/'docker').write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$LOG"\nexit 0\n');(fake/'docker').chmod(0o755)
    env={**os.environ,'PATH':str(fake)+':'+os.environ['PATH'],'LOG':str(log)}
    result=subprocess.run(['bash',str(ROOT/'scripts/docker/smoke-local-image.sh'),'requi-full-candidate:test','linux/arm64'],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    lines=log.read_text().splitlines();build=next(x for x in lines if x.startswith('build '));run=next(x for x in lines if x.startswith('run '))
    assert 'SERVICE_IMAGE=requi-full-candidate:test' in build and 'Dockerfile.pg-smoke' in build
    assert '--network none' in run and '--read-only' in run and '--cap-drop ALL' in run
    assert '--env' not in run and '--platform linux/arm64' in run
    assert '/smoke/owned-pg-smoke.py /smoke python /app/full-smoke.py' in run
    assert lines[-1].startswith('rmi requi-pg-smoke-local:')

def test_failed_smoke_is_failure_and_derivative_is_removed(tmp_path):
    fake=tmp_path/'bin';fake.mkdir();log=tmp_path/'log'
    (fake/'docker').write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$LOG"\n[ "$1" != run ]\n');(fake/'docker').chmod(0o755)
    result=subprocess.run(['bash',str(ROOT/'scripts/docker/smoke-local-image.sh'),'requi-full:test'],env={**os.environ,'PATH':str(fake)+':'+os.environ['PATH'],'LOG':str(log)},capture_output=True,text=True)
    assert result.returncode!=0
    assert log.read_text().splitlines()[-1].startswith('rmi requi-pg-smoke-local:')

def test_supervisor_rejects_inherited_live_dsn_before_starting_pg():
    import sys
    result=subprocess.run([sys.executable,str(ROOT/'scripts/docker/owned-pg-smoke.py'),str(ROOT),sys.executable,'-c','raise AssertionError("should not run")'],env={**os.environ,'DELIVERY_DATABASE_URL':'postgresql://do-not-connect/live'},capture_output=True,text=True)
    assert result.returncode!=0 and 'refuse inherited database configuration' in result.stderr
    assert 'do-not-connect' not in result.stdout+result.stderr

def test_derivative_keeps_the_service_image_and_uses_current_schema():
    text=(ROOT/'scripts/docker/Dockerfile.pg-smoke').read_text()
    assert 'FROM ${SERVICE_IMAGE}' in text and 'install -y --no-install-recommends postgresql' in text
    assert 'COPY supabase/migrations/' in text and 'USER 10001:10001' in text
    service=(ROOT/'Dockerfile.full').read_text()
    assert 'pgserver' not in service and '/smoke/' not in service

def test_system_pg_adapter_migrates_owned_unix_socket_database(tmp_path):
    import sys
    # Exercise the same initdb/pg_ctl adapter as Debian's native image binaries,
    # using the available local PostgreSQL binaries. No external DSN is accepted.
    import pgserver
    bindir=Path(pgserver.__file__).parent/'pginstall/bin'
    env={k:v for k,v in os.environ.items() if k not in ('DELIVERY_DATABASE_URL','DELIVERY_TEST_POSTGRES_DSN','DELIVERY_TEST_POSTGRES_URL')}
    env['REQUI_SMOKE_PG_BIN']=str(bindir)
    proof="import os,psycopg; c=psycopg.connect(os.environ['DELIVERY_TEST_POSTGRES_URL']); assert c.execute('select current_user').fetchone()[0]=='delivery_app'; assert c.execute(\"select to_regclass('tasks') is not null\").fetchone()[0]; print('OWNED-PG-MIGRATED')"
    result=subprocess.run([sys.executable,str(ROOT/'scripts/docker/owned-pg-smoke.py'),str(ROOT),sys.executable,'-c',proof],env=env,capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'OWNED-PG-MIGRATED' in result.stdout


def test_minimal_smoke_context_includes_runtime_inventory_and_migrates(tmp_path):
    import shutil,sys
    context=tmp_path/'context'
    subprocess.run([sys.executable,str(ROOT/'scripts/docker/smoke-context.py'),str(ROOT),str(context)],check=True)
    assert (context/'src/delivery_api/runtime_roles.py').read_bytes()==(ROOT/'src/delivery_api/runtime_roles.py').read_bytes()
    assert 'runtime_roles.py' in (context/'scripts/docker/Dockerfile.pg-smoke').read_text()
    env={k:v for k,v in os.environ.items() if k not in ('DELIVERY_DATABASE_URL','DELIVERY_TEST_POSTGRES_DSN','DELIVERY_TEST_POSTGRES_URL')}
    result=subprocess.run([sys.executable,str(context/'scripts/docker/owned-pg-smoke.py'),str(context),sys.executable,'-c',"print('MINIMAL-CONTEXT-PASSED')"],env=env,capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'MINIMAL-CONTEXT-PASSED' in result.stdout
