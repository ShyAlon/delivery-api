"""Local execution must not inherit inspection-only feature restrictions."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
ROOT=Path(__file__).resolve().parents[1]

def test_execution_preview_can_export_existing_artifacts(monkeypatch,tmp_path):
    import delivery_api.app as module
    monkeypatch.setenv('DELIVERY_LOCAL_PREVIEW','1')
    monkeypatch.setenv('DELIVERY_CLOUD_EXECUTION','1')
    monkeypatch.setattr(module.config,'CLOUD_TENANTS',False)
    monkeypatch.setattr(module,'store',SimpleNamespace(task=lambda _: {'title':'Test'}))
    artifact=tmp_path/'architecture.json';artifact.write_text('{"test":true}')
    monkeypatch.setattr(module,'_task_artifact_snapshot',lambda task_id:[{'phase_key':'architecture','name':artifact.name,'ref':str(artifact)}])
    response=module.task_artifacts_zip('t1')
    assert response.media_type=='application/zip'
    import io,zipfile
    with zipfile.ZipFile(io.BytesIO(response.body)) as z:
        assert z.read('architecture/architecture.json')==artifact.read_bytes()

def test_execution_phase_output_uses_normal_reader(monkeypatch):
    import delivery_api.app as module
    monkeypatch.setenv('DELIVERY_LOCAL_PREVIEW','1')
    monkeypatch.setenv('DELIVERY_CLOUD_EXECUTION','1')
    monkeypatch.setattr(module,'store',SimpleNamespace(task=lambda _: {'active_phase':'srs','generation':3,'phases':[]}))
    monkeypatch.setattr(module,'_recorded_engine_session',lambda _:None)
    assert module.task_phase_output('t1')=={'phase_key':'srs','generation':3,'status':'waiting','items':[]}

def test_default_launcher_opens_both_routes_without_provider_prompt():
    s=(ROOT/'scripts/ops/preview-exec.sh').read_text()
    assert 'PROVIDER=${PREVIEW_EXEC_PROVIDER:-both}' in s
    assert '--memory 3g --memory-swap 3g' in s


def test_wrapper_defaults_both_and_reads_key_as_data(tmp_path):
    import os,sys,subprocess
    root=tmp_path/'repos'/'delivery-api'
    for name in ('delivery-api','interview-engine','spec-generator','verification-loop'):
        (root.parent/name/'.git').mkdir(parents=True)
    ops=root/'scripts'/'ops';ops.mkdir(parents=True)
    (ops/'preview-rebuild.sh').write_text('printf "provider=%s\\n" "$PREVIEW_EXEC_PROVIDER"; test -n "$DEEPSEEK_API_KEY"')
    venv=root/'.venv'/'bin';venv.mkdir(parents=True);(venv/'python').write_text('#!/bin/sh\nexec '+sys.executable+' \"$@\"\n');(venv/'python').chmod(0o755)
    bindir=tmp_path/'bin';bindir.mkdir()
    (bindir/'git').write_text('#!/bin/sh\ncase "$*" in *branch*) echo main;; esac\n')
    (bindir/'docker').write_text('#!/bin/sh\nexit 0\n')
    for f in bindir.iterdir():f.chmod(0o755)
    settings=tmp_path/'preview.env';settings.write_text('DELIVERY_DATABASE_URL=postgresql://fake/db\nDELIVERY_SUPABASE_URL=https://fake\nDELIVERY_SUPABASE_ANON_KEY=fake\n');settings.chmod(0o600)
    key='fake-key-for-hermetic-test-only'
    (root.parent/'.env').write_text('DEEPSEEK_API_KEY='+key+'\n')
    env={'PATH':str(bindir)+':/usr/bin:/bin','HOME':str(tmp_path),'PREVIEW_SETTINGS_FILE':str(settings)}
    p=subprocess.run(['bash',str(ROOT/'scripts/local-test.sh'),str(root)],env=env,capture_output=True,text=True,timeout=15)
    assert p.returncode==0,p.stdout+p.stderr
    assert 'provider=both' in p.stdout
    assert key not in p.stdout+p.stderr
    assert (root.parent/'.env').read_text()=='DEEPSEEK_API_KEY='+key+'\n'


def test_execution_data_and_metrics_survive_container_replacement():
    s=(ROOT/'scripts/ops/preview-exec.sh').read_text()
    assert 'DATA_VOLUME=${PREVIEW_DATA_VOLUME:-requi-preview-data}' in s
    assert '-v "$DATA_VOLUME":/data' in s
    assert 'DELIVERY_METRICS_DB=/data/requi-re/run_metrics.db' in s
    assert 'docker volume rm' not in s
    assert 'docker volume rm' not in (ROOT/'scripts/ops/preview-rebuild.sh').read_text()
