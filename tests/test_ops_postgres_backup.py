import importlib.util,sys,json,os
from pathlib import Path
import pytest
ROOT=Path(__file__).parents[1]
sys.path.append(str(ROOT/'scripts'))  # scripts/ops_common.py, importable the way the scripts run
spec=importlib.util.spec_from_file_location('pg_backup',ROOT/'scripts/ops-postgres-backup.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_pg_dump_credentials_service_only_and_digest_verify_with_stub(tmp_path,monkeypatch):
    service=tmp_path/'pgservice';service.write_text('[fixture]\nhost=unused\npassword=private\n');service.chmod(0o600)
    calls=[]
    def run(command,env):
        calls.append((command,env))
        if command[0]=='pg_dump':Path(command[-1]).write_bytes(b'PGDMP stub archive')
    monkeypatch.setattr(m,'run',run);monkeypatch.setenv('PGPASSWORD','must-not-inherit')
    bundle=tmp_path/'bundle';result=m.snapshot(service,'fixture',bundle)
    assert not result['restore_drill_verified'] and 'private' not in str(result)
    assert calls[0][0][:3]==['pg_dump','--format=custom','--no-password']
    assert calls[0][1]['PGSERVICE']=='fixture' and 'PGPASSWORD' not in calls[0][1]
    assert m.verify(bundle)['archive_integrity_verified']
    (bundle/'database.dump').write_bytes(b'corrupt')
    with pytest.raises(ValueError):m.verify(bundle)


def test_unsafe_pg_service_permissions_and_failed_dump_never_report_success(tmp_path,monkeypatch):
    service=tmp_path/'service';service.write_text('private');service.chmod(0o644)
    with pytest.raises(ValueError):m.snapshot(service,'fixture',tmp_path/'first')
    service.chmod(0o600)
    def fail(*args):raise RuntimeError('stub failure')
    monkeypatch.setattr(m,'run',fail)
    with pytest.raises(RuntimeError):m.snapshot(service,'fixture',tmp_path/'second')
    assert not (tmp_path/'second/manifest.json').exists()


def test_pg_snapshot_dry_cli_does_not_touch_service_or_output(tmp_path):
    import subprocess
    output=tmp_path/'absent'
    result=subprocess.run(['python3',str(ROOT/'scripts/ops-postgres-backup.py'),'snapshot','--service-file',str(tmp_path/'missing'),'--service','fake','--output',str(output)],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)['dry_run'] and not output.exists()
