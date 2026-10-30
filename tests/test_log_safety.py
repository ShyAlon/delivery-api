from delivery_api.log_safety import safe_text,safe_fields
from delivery_api.observability import _line


def test_structured_credentials_payloads_and_fields_are_omitted():
    result=safe_fields({'token':'secret-value','database_url':'postgres://user:pass@host/db','payload':{'text':'private'},'task_id':'task-1'})
    assert result=={'token':'[redacted]','database_url':'[redacted]','payload':'[redacted]','task_id':'task-1'}


def test_message_credentials_query_bearer_newline_and_key_material():
    for text,secret in [('postgresql://user:pass@host/db','pass'),('Bearer abc.xyz','abc.xyz'),
        ('password=hunter','hunter'),('https://host/?access_token=abc','abc')]:
        assert secret not in safe_text(text)
    assert '\n' not in safe_text('hello\nforged log')
    assert safe_text('-----BEGIN PRIVATE KEY----- material')=='[key material omitted]'


def test_log_message_fields_bounded_and_private_objects_not_stringified():
    class Private:
        def __str__(self):raise AssertionError('must not stringify')
    assert safe_text(Private())=='[structured value omitted]'
    assert len(safe_text('x'*10000))<1100
    result=_line('test','failed password=secret',{'Authorization':'Bearer secret','task_id':'task'})
    assert 'secret' not in result and 'task' in result


def test_environment_style_credentials_never_survive_assignment_projection():
    value=safe_text('DEEPSEEK_API_KEY=fixture-A PAYPAL_CLIENT_SECRET=fixture-B TURSO_AUTH_TOKEN=fixture-C')
    assert all(x not in value for x in ('fixture-A','fixture-B','fixture-C'))


def test_stage_pg_retention_is_bounded_and_scrubs_all_writes(tmp_path,monkeypatch,pg_log_evidence):
    from delivery_api import observability as o,postgres_logs
    path=tmp_path/'stage.log';repo=pg_log_evidence.repository;original=repo.append
    def bounded(stream,text,**fields):return original(stream,text,max_bytes=1200,max_rows=3,**fields)
    repo.append=bounded
    monkeypatch.setattr(postgres_logs,'selected',lambda:repo)
    monkeypatch.setattr(o,'STAGE_LOG_PATHS',{'needs':path})
    for i in range(20):o.event('fixture','API_KEY=must-not-leak '+'x'*80,count=i,phase_key='needs')
    rows=repo.tail('stage:needs')
    assert len(rows)==3 and sum(len(row['text'].encode()) for row in rows)<=1200
    assert all('must-not-leak' not in row['text'] for row in rows)
    assert not list(tmp_path.glob('stage.log*'))
