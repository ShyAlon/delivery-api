"""Real migrations and restricted roles; no file fallback or customer reads."""
import uuid
import pytest
from delivery_api.postgres_logs import flush
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
    assert flush(5)
    monkeypatch.setenv('DELIVERY_LOG_SOURCE','')
    assert logs.emit('activity','lost') is True, 'selected PG failure must never invite a file fallback'
    assert flush(5)
    assert any(row['source']=='postgres-logs' for row in snapshot()['sources'])
    monkeypatch.setattr(config,'DATABASE_URL','sqlite:///'+str(tmp_path/'legacy.db'))
    assert logs.selected() is None and logs.emit('activity','legacy') is False








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
    assert flush(5)
    assert len(calls)==1 and not getattr(logs._emitting,'active',False)


def test_per_stream_retention_uses_the_existing_total_budget_and_not_one_budget_each():
    from delivery_api.postgres_logs import retention
    from delivery_api.log_budget import BUDGET
    assert retention('diagnostics')==BUDGET['diagnostics'][0]*BUDGET['diagnostics'][1]
    assert retention('activity')==BUDGET['activity'][0]*BUDGET['activity'][1]
    assert retention('engine')==BUDGET['engine'][0]*BUDGET['engine'][1]
    assert retention('stage:srs')==BUDGET['stages'][0]*BUDGET['stages'][1]//8
    assert sum(retention(s) for s in ('diagnostics','activity','engine',*('stage:'+p for p in ('intent','conops','needs','srs','architecture','specs','tests','validation'))))==32*1024*1024








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
