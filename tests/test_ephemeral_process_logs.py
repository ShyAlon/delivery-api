import asyncio,time
from delivery_api import process_logs

def test_runtime_log_never_calls_pg_and_keeps_loop_responsive(monkeypatch,tmp_path):
    from delivery_api import postgres_logs
    monkeypatch.setattr(postgres_logs.PostgresLogs,'append',lambda *a,**k:(_ for _ in ()).throw(AssertionError('DB write')))
    monkeypatch.setattr(process_logs,'root',lambda:tmp_path)
    original=process_logs.FileLogs.append
    def slow(*a,**kw):time.sleep(.15);return original(*a,**kw)
    monkeypatch.setattr(process_logs.FileLogs,'append',slow)
    async def run():
        start=time.perf_counter()
        for _ in range(3):process_logs.emit('activity','test password=hidden')
        return time.perf_counter()-start
    elapsed=asyncio.run(run());print('enqueue_3_seconds',elapsed)
    assert elapsed<.05
    assert process_logs.flush(3)
    rows=process_logs.selected().tail('activity')
    assert len(rows)==3 and 'hidden' not in str(rows)


def test_files_survive_new_reader_and_preserve_projection(monkeypatch,tmp_path):
    import json
    monkeypatch.setattr(process_logs,'root',lambda:tmp_path)
    repo=process_logs.FileLogs()
    repo.append('diagnostics',json.dumps({'message':'password=hidden','frames':[{'file':'a.py','function':'f','line':42}]}),task_id='task-a')
    row=process_logs.FileLogs().records(task_id='task-a')['records'][0]
    assert 'hidden' not in row['text'] and json.loads(row['text'])['frames'][0]['line']==42
    import pytest
    with pytest.raises(ValueError):repo.append('diagnostics','{"unreviewed":"field"}')
    assert process_logs.FileLogs().records(task_id='other')['records']==[]


def test_debug_uses_file_rows_and_enforces_registry(monkeypatch,file_log_evidence):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from delivery_api import app,config
    repo=file_log_evidence.repository
    repo.append('diagnostics','{"message":"safe marker"}',task_id='task-a')
    calls=[]
    class Reader:
        @contextmanager
        def _read(self,store,cap):
            calls.append(cap);yield None,{'registry_version':'test'}
    monkeypatch.setattr(config,'CLOUD_TENANTS',True)
    monkeypatch.setattr(app,'_operator_reader',lambda:Reader())
    result=app.debug_evidence('task-a',SimpleNamespace(admin=True))
    assert result['count']==1 and 'safe marker' in result['text'] and calls==['logs.read']
    import pytest
    with pytest.raises(app.HTTPException):app.debug_evidence(None,SimpleNamespace(admin=False))


def test_file_download_omits_engine_and_frame_source(monkeypatch,file_log_evidence):
    from contextlib import contextmanager
    repo=file_log_evidence.repository
    repo.append('engine','private source stdout')
    repo.append('diagnostics','{"message":"safe marker","frames":[{"file":"private.py","line":1}]}')
    class Reader:
        @contextmanager
        def _read(self,*a):yield None,{'registry_version':'test'}
    result=process_logs.operator_view(Reader(),None,30,download=True)
    assert 'safe marker' in result['text']
    assert 'private' not in result['text']


def test_runtime_log_callsites_never_import_pg_log_writer():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    for relative in ('src/delivery_api/app.py','src/delivery_api/observability.py',
                     'src/delivery_api/diagnostic_evidence.py','src/delivery_api/engine_log.py',
                     'scripts/docker/full_entry.py','src/delivery_api/preflight_read.py'):
        assert 'postgres_logs import' not in (root/relative).read_text()


def test_file_rotation_stays_bounded_and_disk_failure_is_visible(monkeypatch,file_log_evidence):
    from delivery_api import log_durability
    monkeypatch.setattr(process_logs,'retention',lambda stream:2048)
    for n in range(50):process_logs.emit('activity','line '+str(n)+' x'*100)
    assert process_logs.flush(5)
    paths=list(process_logs.root().glob('*.jsonl*'))
    assert len(paths)==3 # active, backup, stable lock file
    assert sum(p.stat().st_size for p in paths)<=2048
    monkeypatch.setattr(log_durability,'_failures',{})
    monkeypatch.setattr(process_logs.FileLogs,'append',lambda *a,**k:(_ for _ in ()).throw(OSError('private')))
    process_logs.emit('activity','disk failed');assert process_logs.flush(3)
    assert log_durability.snapshot()['sources'][0]['source']=='process-files'


def test_navigation_export_reads_only_bounded_retained_activity(tmp_path,monkeypatch):
    from delivery_api import process_logs as logs
    monkeypatch.setattr(logs,'root',lambda:tmp_path)
    store=logs.FileLogs()
    store.append('activity','client.ui.activation Selection gesture ignored target=input',ts=1)
    store.append('activity','ordinary task event',ts=2)
    store.append('activity','client.navigation.caller router.navigate dashboard.js',ts=3)
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    result=logs.navigation_export()
    assert len(result['records'])==2 and not result['unreadable']
    assert before=={p.name:p.read_bytes() for p in tmp_path.iterdir()}
