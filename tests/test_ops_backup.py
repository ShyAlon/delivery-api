import importlib.util,json,sqlite3,subprocess
from pathlib import Path
import pytest
ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('ops_backup',ROOT/'scripts/ops-backup.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def db(tmp_path):
    path=tmp_path/'source.sqlite'
    with sqlite3.connect(path) as c:c.execute('CREATE TABLE account(id TEXT PRIMARY KEY,balance INTEGER)');c.execute("INSERT INTO account VALUES('owner',17)")
    return path


def test_snapshot_and_new_directory_restore_drill_preserve_exact_source(tmp_path):
    source=db(tmp_path);before=module.digest(source);bundle=tmp_path/'backup'
    result=module.backup(source,bundle)
    assert not result['cloud_backup'] and not result['artifacts_included']
    assert module.verify(bundle)['sha256']==module.digest(bundle/'database.sqlite')
    assert module.restore_drill(bundle,tmp_path/'drill')['live_restore'] is False
    assert module.digest(source)==before
    with sqlite3.connect(tmp_path/'drill/drill.sqlite') as c:assert c.execute('SELECT balance FROM account').fetchone()[0]==17
    assert (bundle/'database.sqlite').stat().st_mode&0o777==0o600


def test_dry_default_never_reads_source_or_creates_output(tmp_path):
    output=tmp_path/'out'
    result=subprocess.run(['python3',str(ROOT/'scripts/ops-backup.py'),'snapshot','--source',str(tmp_path/'missing'),'--output',str(output)],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)['dry_run'] and not output.exists()


def test_corruption_manifest_path_symlink_and_overwrite_fail_closed(tmp_path):
    source=db(tmp_path);bundle=tmp_path/'backup';module.backup(source,bundle)
    with pytest.raises(ValueError):module.backup(source,bundle)
    (bundle/'database.sqlite').write_bytes(b'corrupt')
    with pytest.raises(ValueError):module.verify(bundle)
    manifest=json.loads((bundle/'manifest.json').read_text());manifest['file']='../source.sqlite';(bundle/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):module.verify(bundle)
    link=tmp_path/'link';link.symlink_to(source)
    with pytest.raises(ValueError):module.backup(link,tmp_path/'other')


def test_source_wal_transactions_are_copied_by_sqlite_backup_api(tmp_path):
    source=db(tmp_path)
    with sqlite3.connect(source) as c:
        c.execute('PRAGMA journal_mode=WAL');c.execute("UPDATE account SET balance=42");c.commit()
        module.backup(source,tmp_path/'bundle')
        with sqlite3.connect(tmp_path/'bundle/database.sqlite') as check:assert check.execute('SELECT balance FROM account').fetchone()[0]==42


def test_restore_drill_refuses_existing_target_without_changing_source(tmp_path):
    source=db(tmp_path);bundle=tmp_path/'bundle';module.backup(source,bundle)
    target=tmp_path/'existing';target.mkdir();sentinel=target/'important';sentinel.write_text('keep')
    with pytest.raises(ValueError):module.restore_drill(bundle,target)
    assert sentinel.read_text()=='keep'


def test_backup_parent_symlink_rejected(tmp_path):
    source=db(tmp_path);actual=tmp_path/'actual';actual.mkdir();link=tmp_path/'link';link.symlink_to(actual,target_is_directory=True)
    with pytest.raises(ValueError):module.backup(source,link/'bundle')
    assert not (actual/'bundle').exists()
