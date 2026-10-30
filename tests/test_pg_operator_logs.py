"""Real migrations and restricted roles; no file fallback or customer reads."""
import uuid
import pytest
import psycopg
from pg_harness import STATE
from delivery_api.tenancy import tenant_context
from test_postgres_store import A,B


def repository(source=None):
    from delivery_api.postgres_logs import PostgresLogs
    return PostgresLogs(STATE['dsn'].replace('user=sandbox','user=delivery_app'),source_id=source or uuid.uuid4().hex)


def test_exact_scrubbed_rows_restart_and_source_isolation():
    first=repository();first.append('activity','failure password=secret-value',task_id='task',session_id='session')
    reopened=repository(first.source_id);other=repository()
    with reopened.transaction() as c:
        rows=c.execute('SELECT stream,text,task_id,session_id FROM operator_log_records ORDER BY id').fetchall()
        assert len(rows)==1 and rows[0]['stream']=='activity'
        assert 'secret-value' not in rows[0]['text'] and rows[0]['task_id']=='task'
    with other.transaction() as c:assert c.execute('SELECT count(*) n FROM operator_log_records').fetchone()['n']==0


def test_writer_cannot_read_task_or_credit_data_and_customer_roles_cannot_read_logs():
    repo=repository();repo.append('diagnostics','{"message":"safe failure"}')
    with repo.transaction() as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.transaction():c.execute('SELECT * FROM credit_accounts')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.transaction():c.execute('SELECT * FROM tasks')
    for role in ('requi_backend','authenticated','anon','service_role'):
        with psycopg.connect(STATE['dsn']) as c:
            c.execute('SET LOCAL ROLE '+role)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute('SELECT * FROM operator_log_records')


def test_retention_is_bounded_per_source_stream_and_append_is_atomic():
    repo=repository();other=repository();other.append('activity','untouched')
    for n in range(8):repo.append('activity',('message-'+str(n)+' ')*50,max_bytes=1250,max_rows=3)
    with repo.transaction() as c:
        rows=c.execute('SELECT text FROM operator_log_records ORDER BY id').fetchall()
        assert len(rows)==2 and rows[0]['text']==('message-6 ')*50 and rows[1]['text']==('message-7 ')*50
    with other.transaction() as c:assert c.execute('SELECT count(*) n FROM operator_log_records').fetchone()['n']==1
    with pytest.raises(RuntimeError):
        with repo.transaction() as c:
            c.execute("INSERT INTO operator_log_records(source_id,stream,ts,text,byte_size) VALUES(%s,'activity',1,'rollback',8)",(repo.source_id,))
            raise RuntimeError('rollback')
    with repo.transaction() as c:assert c.execute('SELECT count(*) n FROM operator_log_records').fetchone()['n']==2


def test_invalid_inputs_never_open_db_or_fallback(tmp_path):
    from delivery_api.postgres_logs import PostgresLogs
    with pytest.raises(ValueError):PostgresLogs('sqlite:///'+str(tmp_path/'logs.db'),source_id='host')
    with pytest.raises(ValueError):PostgresLogs(STATE['dsn'],source_id='')
    repo=repository()
    for stream in ('../other','stage:unknown','customer'):
        with pytest.raises(ValueError):repo.append(stream,'no')
    with pytest.raises(ValueError):repo.append('activity','ok',max_rows=0)
    assert not list(tmp_path.iterdir())


def test_operator_reader_requires_live_logs_capability_and_revocation():
    from delivery_api.operator_reads import OperatorReadStore
    from delivery_api.operator_authorization import OperatorPrincipal
    from delivery_api.postgres_store import PostgresStore
    from delivery_api.tenancy import TenantAccessDenied
    repo=repository();repo.append('diagnostics','{"message":"owned record"}',task_id='task-x')
    owner=PostgresStore(STATE['dsn'].replace('user=sandbox','user=delivery_app'))
    with psycopg.connect(STATE['dsn']) as c:
        c.execute('DELETE FROM operator_principals')
        c.execute("INSERT INTO operator_principals(subject,registry_version,capabilities,enabled) VALUES(%s,'log-test',ARRAY['logs.read'],true)",(A.owner_id,))
    reader=OperatorReadStore(repo.dsn,OperatorPrincipal(A.owner_id,'log-test',frozenset({'logs.read'})))
    try:
        with tenant_context(A):
            result=reader.logs(owner,stream='diagnostics',task_id='task-x',limit=2)
            assert len(result['records'])==1 and __import__('json').loads(result['records'][0]['text'])['message']=='owned record'
        with tenant_context(B),pytest.raises(TenantAccessDenied):reader.logs(owner,stream='diagnostics')
        with psycopg.connect(STATE['dsn']) as c:c.execute('UPDATE operator_principals SET enabled=false')
        with tenant_context(A),pytest.raises(TenantAccessDenied):reader.logs(owner,stream='diagnostics')
    finally:
        with psycopg.connect(STATE['dsn']) as c:c.execute('DELETE FROM operator_principals')


def test_default_pg_runtime_log_routing_refuses_file_fallback_and_records_degradation(monkeypatch,tmp_path):
    from delivery_api import postgres_logs as logs
    from delivery_api import config
    from delivery_api.log_durability import snapshot
    monkeypatch.setattr(config,'DATABASE_URL',STATE['dsn'].replace('user=sandbox','user=delivery_app'))
    monkeypatch.setenv('DELIVERY_LOG_SOURCE','runtime-test')
    assert logs.selected() is not None
    assert logs.emit('activity','first',task_id='task')
    monkeypatch.setenv('DELIVERY_LOG_SOURCE','')
    assert logs.emit('activity','lost') is True, 'selected PG failure must never invite a file fallback'
    assert any(row['source']=='postgres-logs' for row in snapshot()['sources'])
    monkeypatch.setattr(config,'DATABASE_URL','sqlite:///'+str(tmp_path/'legacy.db'))
    assert logs.selected() is None and logs.emit('activity','legacy') is False


def test_pg_activity_stage_and_diagnostic_handlers_never_create_files(monkeypatch,tmp_path):
    import logging
    from delivery_api import config,observability,diagnostic_evidence
    monkeypatch.setattr(config,'DATABASE_URL',STATE['dsn'].replace('user=sandbox','user=delivery_app'))
    monkeypatch.setenv('DELIVERY_LOG_SOURCE','runtime-routing')
    path=tmp_path/'forbidden.log'
    handler=observability._EvidenceFileHandler(path,delay=True)
    handler.emit(logging.makeLogRecord({'levelno':20,'msg':'activity password=secret'}))
    diagnostic=diagnostic_evidence.EvidenceHandler(tmp_path/'forbidden-diagnostic.jsonl')
    diagnostic.emit(logging.makeLogRecord({'name':'test','levelno':40,'levelname':'ERROR','msg':'diagnostic'}))
    monkeypatch.setattr(observability,'STAGE_LOG_PATHS',{'srs':tmp_path/'stages/srs.log'})
    observability.event('test','stage row',phase='srs',task_id='owned')
    assert not list(tmp_path.iterdir())
    repo=repository('runtime-routing')
    with repo.transaction() as c:
        streams={r['stream'] for r in c.execute('SELECT stream FROM operator_log_records').fetchall()}
    assert {'activity','diagnostics','stage:srs'}<=streams


def test_pg_engine_mirror_never_materializes_file_and_keeps_source_bound_rows(monkeypatch,tmp_path):
    import logging
    from delivery_api import config,engine_log
    monkeypatch.setattr(config,'DATABASE_URL',STATE['dsn'].replace('user=sandbox','user=delivery_app'))
    monkeypatch.setenv('DELIVERY_LOG_SOURCE','engine-routing')
    handler=engine_log._MirrorHandler(tmp_path/'engine.log',logging.INFO)
    handler.emit(logging.makeLogRecord({'name':'interview_engine','levelno':30,'levelname':'WARNING','msg':'lease lost session=11111111-1111-4111-8111-111111111111'}))
    assert not list(tmp_path.iterdir())
    with repository('engine-routing').transaction() as c:
        row=c.execute("SELECT text,session_id FROM operator_log_records WHERE stream='engine'").fetchone()
        assert 'lease lost' in row['text'] and row['session_id']=='11111111-1111-4111-8111-111111111111'


def test_diagnostic_projection_remains_parseable_without_secret_or_truncated_json(monkeypatch):
    import logging,json
    from delivery_api import config,diagnostic_evidence
    monkeypatch.setattr(config,'DATABASE_URL',STATE['dsn'].replace('user=sandbox','user=delivery_app'))
    monkeypatch.setenv('DELIVERY_LOG_SOURCE','json-projection')
    handler=diagnostic_evidence.EvidenceHandler()
    handler.emit(logging.makeLogRecord({'name':'test','levelno':40,'levelname':'ERROR','msg':'password=secret-value '+'ordinary words '*200}))
    with repository('json-projection').transaction() as c:
        row=c.execute("SELECT text FROM operator_log_records WHERE stream='diagnostics'").fetchone()
    parsed=json.loads(row['text'])
    assert parsed['logger']=='test' and parsed['level']=='ERROR'
    assert 'secret-value' not in parsed['message'] and len(parsed['message'])<=2100


def test_log_failure_is_nonrecursive_and_does_not_borrow_while_another_log_is_emitting(monkeypatch):
    from delivery_api import postgres_logs as logs,config
    monkeypatch.setattr(config,'DATABASE_URL',STATE['dsn'].replace('user=sandbox','user=delivery_app'))
    monkeypatch.setenv('DELIVERY_LOG_SOURCE','recursive-test')
    calls=[]
    def recursive(self,*args,**kwargs):
        calls.append(args)
        assert logs.emit('activity','recursive') is True
        raise ConnectionError('private database failure')
    monkeypatch.setattr(logs.PostgresLogs,'append',recursive)
    assert logs.emit('activity','first') is True
    assert len(calls)==1 and not getattr(logs._emitting,'active',False)


def test_per_stream_retention_uses_the_existing_total_budget_and_not_one_budget_each():
    from delivery_api.postgres_logs import retention
    from delivery_api.log_budget import BUDGET
    assert retention('diagnostics')==BUDGET['diagnostics'][0]*BUDGET['diagnostics'][1]
    assert retention('activity')==BUDGET['activity'][0]*BUDGET['activity'][1]
    assert retention('engine')==BUDGET['engine'][0]*BUDGET['engine'][1]
    assert retention('stage:srs')==BUDGET['stages'][0]*BUDGET['stages'][1]//8
    assert sum(retention(s) for s in ('diagnostics','activity','engine',*('stage:'+p for p in ('intent','conops','needs','srs','architecture','specs','tests','validation'))))==32*1024*1024


def test_pg_engine_read_keeps_customer_session_audience_and_operator_system_lines(monkeypatch,tmp_path):
    from delivery_api import config,engine_log
    monkeypatch.setattr(config,'DATABASE_URL',STATE['dsn'].replace('user=sandbox','user=delivery_app'))
    monkeypatch.setenv('DELIVERY_LOG_SOURCE','engine-reader')
    sid='11111111-1111-4111-8111-111111111111';other='22222222-2222-4222-8222-222222222222'
    repo=repository('engine-reader')
    repo.append('engine','session='+sid+' owned line',session_id=sid)
    repo.append('engine','session='+other+' foreign line',session_id=other)
    repo.append('engine','system line')
    monkeypatch.setattr(engine_log,'_tail',lambda path:(_ for _ in ()).throw(AssertionError('file read forbidden')))
    resolve=lambda session:'owner' if session==sid else 'other'
    own=engine_log.read('owner',False,30,resolve)
    assert own['available'] and 'owned line' in own['text'] and 'foreign line' not in own['text'] and 'system line' not in own['text']
    admin=engine_log.read('owner',True,30,resolve)
    assert 'foreign line' in admin['text'] and 'system line' in admin['text']


def test_debug_evidence_pg_path_uses_registry_reader_not_local_file(monkeypatch):
    from types import SimpleNamespace
    from delivery_api import app,config,diagnostic_evidence
    seen=[]
    class Reader:
        def logs(self,store,**kwargs):
            seen.append(kwargs)
            return {'records':[{'text':'{"message":"safe"}'}],'truncated':False,'scope':'reviewed_cross_user_operator_read'}
    monkeypatch.setattr(config,'CLOUD_TENANTS',True)
    monkeypatch.setattr(app,'_operator_reader',lambda:Reader())
    monkeypatch.setattr(diagnostic_evidence,'read',lambda **kw:(_ for _ in ()).throw(AssertionError('legacy file read forbidden')))
    result=app.debug_evidence('task-x',SimpleNamespace(admin=True))
    assert result['available'] and result['count']==1 and 'safe' in result['text']
    assert seen==[{'stream':'diagnostics','task_id':'task-x','limit':500}]


def test_pg_full_service_engine_tee_preserves_stdout_without_file(monkeypatch,tmp_path):
    import importlib.util,subprocess,sys,time
    from pathlib import Path
    from delivery_api import config
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('pg_full_entry',root/'scripts/docker/full_entry.py')
    entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
    monkeypatch.setattr(config,'DATABASE_URL',STATE['dsn'].replace('user=sandbox','user=delivery_app'))
    monkeypatch.setenv('DELIVERY_LOG_SOURCE','tee-test')
    class Output:
        def __init__(self):self.rows=[]
        def write(self,line):self.rows.append(line)
        def flush(self):pass
    output=Output()
    process=subprocess.Popen([sys.executable,'-c','print("hello engine")'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    entry.tee_engine_output(process,tmp_path/'engine.log',out=output);process.wait()
    for _ in range(50):
        if process.stdout.closed:break
        time.sleep(.02)
    assert ''.join(output.rows)=='hello engine\n'
    assert not list(tmp_path.iterdir())
    with repository('tee-test').transaction() as c:
        assert c.execute("SELECT text FROM operator_log_records WHERE stream='engine'").fetchone()['text']=='hello engine'


def test_pg_log_transaction_has_bounded_statement_and_lock_waits():
    with repository().transaction() as c:
        assert c.execute('SHOW statement_timeout').fetchone()['statement_timeout']=='500ms'
        assert c.execute('SHOW lock_timeout').fetchone()['lock_timeout']=='100ms'


def test_default_runtime_source_is_stable_across_hosts_and_processes(monkeypatch):
    from delivery_api import config,postgres_logs as logs
    monkeypatch.setattr(config,'DATABASE_URL',STATE['dsn'].replace('user=sandbox','user=delivery_app'))
    monkeypatch.delenv('DELIVERY_LOG_SOURCE',raising=False)
    first=logs.selected().source_id
    monkeypatch.setenv('HOSTNAME','ephemeral-replacement-host')
    assert first==logs.selected().source_id=='delivery-runtime'
    monkeypatch.setenv('DELIVERY_LOG_SOURCE','')
    with pytest.raises(ValueError):logs.selected()


def test_diagnostic_frames_keep_integer_line_and_reject_unknown_or_malformed_fields():
    import json
    repo=repository()
    repo.append('diagnostics',json.dumps({'message':'safe','frames':[{'file':'file.py','function':'fn','line':42}]}))
    row=repo.tail('diagnostics')[0]
    assert json.loads(row['text'])['frames'][0]['line']==42
    for frame in ({'source':'private code'},'bad',{'line':True},{'line':-2}):
        with pytest.raises(ValueError):repo.append('diagnostics',json.dumps({'frames':[frame]}))
    with pytest.raises(ValueError):repo.append('diagnostics',json.dumps({'frames':'bad'}))


def test_database_rejects_nonfinite_timestamps_even_for_direct_writer():
    repo=repository()
    for stamp in (float('nan'),float('inf'),float('-inf'),-1):
        with repo.transaction() as c,pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO operator_log_records(source_id,stream,ts,text,byte_size) VALUES(%s,'activity',%s,'x',1)",(repo.source_id,stamp))


def test_pg_operator_server_log_and_download_read_crosshost_registry_rows_only(monkeypatch):
    from types import SimpleNamespace
    from delivery_api import app,config,engine_log,server_log_buffer
    import time,io,zipfile
    monkeypatch.setattr(config,'CLOUD_TENANTS',True)
    calls=[]
    class Reader:
        def logs(self,store,**kw):
            calls.append(kw)
            return {'records':[{'stream':'activity','ts':time.time(),'text':'cross-host activity'},
                               {'stream':'diagnostics','ts':time.time(),'text':'{"logger":"lib","level":"ERROR","message":"'+'x'*300+'","frames":[{"file":"private.py"}]}'}],
                    'truncated':True,'registry_version':'v1'}
    monkeypatch.setattr(app,'_operator_reader',lambda:Reader())
    monkeypatch.setattr(server_log_buffer,'read',lambda *a:(_ for _ in ()).throw(AssertionError('memory read forbidden')))
    monkeypatch.setattr(engine_log,'read',lambda *a:(_ for _ in ()).throw(AssertionError('source writer read forbidden')))
    actor=SimpleNamespace(id='operator',admin=True,label='operator')
    result=app.server_log(30,actor,None)
    assert 'cross-host activity' in result['text'] and result['truncated']
    response=app.admin_operations_log_download(30,actor)
    with zipfile.ZipFile(io.BytesIO(response.body)) as z:
        text=z.read(z.namelist()[0]).decode()
    assert 'cross-host activity' in text and 'private.py' not in text and 'x'*170 not in text
    assert len(calls)==2


def test_pg_engine_mirror_preserves_traceback_rows_instead_of_one_truncated_envelope(monkeypatch,tmp_path):
    import logging,sys
    from delivery_api import config,engine_log
    monkeypatch.setattr(config,'DATABASE_URL',STATE['dsn'].replace('user=sandbox','user=delivery_app'))
    monkeypatch.setenv('DELIVERY_LOG_SOURCE','traceback-rows')
    try:raise ValueError('safe failure marker')
    except ValueError:info=sys.exc_info()
    handler=engine_log._MirrorHandler(tmp_path/'forbidden.log',logging.INFO)
    handler.emit(logging.makeLogRecord({'name':'interview_engine','levelno':40,'levelname':'ERROR','msg':'failed session=11111111-1111-4111-8111-111111111111','exc_info':info}))
    rows=repository('traceback-rows').tail('engine')
    assert len(rows)>=3 and any('ValueError: safe failure marker' in r['text'] for r in rows)
    assert not (tmp_path/'forbidden.log').exists()


def test_pg_startup_report_restart_read_without_local_copy(monkeypatch,tmp_path):
    import importlib.util
    from pathlib import Path
    from delivery_api import config,preflight_read
    path=Path(__file__).resolve().parents[1]/'scripts/docker/preflight.py'
    spec=importlib.util.spec_from_file_location('startup_pg_report',path)
    preflight=importlib.util.module_from_spec(spec);__import__('sys').modules[spec.name]=preflight;spec.loader.exec_module(preflight)
    monkeypatch.setattr(config,'DATABASE_URL',STATE['dsn'].replace('user=sandbox','user=delivery_app'))
    monkeypatch.setenv('DELIVERY_LOG_SOURCE','startup-report')
    target=tmp_path/'forbidden.json'
    report=preflight.Report(generated_at='2026-10-30T01:00:00Z')
    report.checks.append(preflight.Check('safe-check','warn','pass','check','expected','found','where'))
    assert preflight.write_report(report,target)
    assert not target.exists()
    assert preflight_read.startup_report()==report.to_dict()
    report.generated_at='2026-10-30T02:00:00Z'
    assert preflight.write_report(report,target)
    assert preflight_read.startup_report()['generated_at']==report.generated_at
    with repository('startup-report').transaction() as c:
        assert c.execute('SELECT count(*) n FROM operator_startup_reports').fetchone()['n']==1


def test_operator_download_excludes_unstructured_engine_trace_source(monkeypatch):
    from delivery_api.postgres_logs import operator_view
    import time
    class Reader:
        def logs(self,*a,**kw):
            return {'records':[{'stream':'engine','ts':time.time(),'text':'    private_source_call()'},
                               {'stream':'activity','ts':time.time(),'text':'safe event'}],
                    'truncated':False,'registry_version':'v1'}
    assert 'private_source_call' in operator_view(Reader(),None,30)['text']
    assert 'private_source_call' not in operator_view(Reader(),None,30,download=True)['text']


def test_real_pg_log_lock_timeout_keeps_previous_evidence_and_rollback():
    import time
    repo=repository();repo.append('activity','before contention')
    with repo.transaction() as held:
        held.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('operator-log:'+repo.source_id+':activity',))
        started=time.monotonic()
        with pytest.raises(psycopg.errors.LockNotAvailable):repo.append('activity','must rollback')
        assert time.monotonic()-started<1.0
    assert [r['text'] for r in repo.tail('activity')]==['before contention']


def test_startup_projection_scrubs_credentials_and_customer_role_cannot_read():
    repo=repository();repo.save_startup({'generated_at':'test','status':'warn','checks':[{'found':'password=must-not-persist postgres://u:secret@host/db'}]})
    assert 'must-not-persist' not in str(repo.startup()) and ':secret@' not in str(repo.startup())
    with repository().transaction() as c:
        assert c.execute('SELECT count(*) n FROM operator_startup_reports').fetchone()['n']==0
    with psycopg.connect(STATE['dsn']) as c:
        c.execute('SET LOCAL ROLE requi_backend')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute('SELECT * FROM operator_startup_reports')
