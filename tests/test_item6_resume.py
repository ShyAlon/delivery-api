import json,os
import pytest
import resume_entry as entry
import recovery_resume as resume
from delivery_api.attempt_ledger import AdmissionDenied

RID='1e4750fe18ff4f29aecfabd698a77a13'


class Result:
    def __init__(self,rows):self.rows=rows
    def fetchall(self):return list(self.rows)
    def fetchone(self):return self.rows[0] if self.rows else None


class Conn:
    """Canned rows keyed by a table name found in the SQL text."""
    def __init__(self,**tables):self.tables=tables;self.calls=[]
    def execute(self,sql,params=()):
        self.calls.append(sql)
        if 'job_id<>' in sql:return Result(self.tables.get('other_segments',[]))
        for name,rows in self.tables.items():
            if 'FROM '+name in sql:return Result(rows)
        return Result([])


def job(kind,state,jid):return {'job_id':jid,'kind':kind,'state':state,'task_id':'t1','attempt':1,'generation':1,'session_id':'s1','auth_session_id':'a1'}


def attempt(state='settled',**extra):
    row={'attempt_id':'x'*32,'state':state,'job_id':'J2','expense_reserved':16932600000,'retail_reserved':1,'expense_actual':5 if state=='settled' else None,
        'retail_actual':None,'usage':None,'raw_usage':None,'raw_usage_received_at':None,'settled_at':None,'provider_started_at':None}
    return row|extra


def conn(**over):
    base=dict(worker_jobs=[job('phase:intent','done','J1'),job('phase:needs','failed','J2')],
        provider_attempts=[attempt('settled',attempt_id='s'*32,job_id='J1'),attempt('reserved',attempt_id='r'*32)],
        retail_compute_segments=[{'state':'unresolved','deterministic_ms':None}],
        credit_accounts=[{'owner_id':'o1','balance_units':-47299513,'units_per_credit':10**9}])
    base.update(over);return Conn(**base)


def test_discover_returns_the_exact_stopped_facts():
    facts=resume.discover(conn())
    assert facts['failed_job']=='J2' and facts['attempt_id']=='r'*32 and facts['settled_attempts']==1
    assert facts['compute_key']=='J2:compute' and facts['balance_units']==-47299513


@pytest.mark.parametrize('over,message',[
 (dict(worker_jobs=[job('phase:intent','done','J1')]),'job chain'),
 (dict(worker_jobs=[job('phase:intent','done','J1'),job('phase:needs','done','J2')]),'failed needs'),
 (dict(provider_attempts=[attempt('reserved',attempt_id='a'*32),attempt('reserved',attempt_id='b'*32)]),'exactly one'),
 (dict(provider_attempts=[attempt('dispatched')]),'never-started'),
 (dict(provider_attempts=[attempt('reserved',provider_started_at=1.0)]),'never-started'),
 (dict(provider_attempts=[attempt('reserved',job_id='J1')]),'never-started'),
 (dict(provider_attempts=[attempt('reserved',usage={'a':1})]),'usage'),
 (dict(credit_charges=[{'x':1}]),'prepaid charge'),
 (dict(retail_contributions=[{'x':1}]),'token contribution'),
 (dict(audit_log=[{'x':1}]),'already has'),
 (dict(retail_compute_segments=[{'state':'settled','deterministic_ms':5}]),'unresolved'),
 (dict(retail_compute_segments=[]),'unresolved'),
 (dict(other_segments=[{'x':1}]),'another compute'),
 (dict(credit_accounts=[{'owner_id':'o1','balance_units':1,'units_per_credit':1000}]),'scale'),
])
def test_discover_refuses_anything_but_the_exact_stopped_shape(over,message):
    with pytest.raises(AdmissionDenied,match=message):resume.discover(conn(**over))


def test_typed_yes_is_the_only_confirmation_and_the_reference_names_it():
    facts=resume.discover(conn());lines=[]
    ref=resume.confirm(facts,'a'*64,RID,ask=lambda prompt:'yes',say=lines.append,now='2026-10-16T16:00:00+0300')
    assert ref.startswith('typed-yes:2026-10-16T16:00:00+0300:run-'+RID) and 'attempt-rrrrrrrr' in ref
    text='\n'.join(lines)
    assert 'r'*32 in text and 'J2:compute' in text and 'billed 0' in text and 'a'*64 in text


@pytest.mark.parametrize('answer',['','y','YES','no','yes please','GO'])
def test_anything_else_writes_nothing(answer):
    facts=resume.discover(conn())
    with pytest.raises(AdmissionDenied,match='not confirmed'):resume.confirm(facts,'a'*64,RID,ask=lambda prompt:answer,say=lambda line:None)


def paths(tmp_path):
    root=tmp_path/'evidence'/'run';root.mkdir(parents=True);(root/'blocked.json').write_text('{}')
    (tmp_path/'evidence'/'item6-database.dump').write_bytes(b'x')
    return entry.plan_paths({'root':str(root)})


def cfg():return {'database_name':'requi_item6_'+RID,'database_host':'127.0.0.1'}


def test_preflight_accepts_a_stopped_run_with_a_fresh_webhook_folder(tmp_path,monkeypatch):
    p=paths(tmp_path);hook=tmp_path/'hook';hook.mkdir();monkeypatch.setenv('ITEM6_WEBHOOK_DIR',str(hook))
    entry.preflight(cfg(),RID,p,data=tmp_path/'pg')


def test_preflight_refuses_a_second_resume_without_overwriting(tmp_path,monkeypatch):
    p=paths(tmp_path);hook=tmp_path/'hook';hook.mkdir();monkeypatch.setenv('ITEM6_WEBHOOK_DIR',str(hook))
    p['new_dump'].write_bytes(b'old')
    with pytest.raises(ValueError,match='already ran'):entry.preflight(cfg(),RID,p,data=tmp_path/'pg')
    assert p['new_dump'].read_bytes()==b'old'


@pytest.mark.parametrize('setup',['unset','private','missing','nonempty'])
def test_preflight_never_reuses_the_first_purchase_export(tmp_path,monkeypatch,setup):
    p=paths(tmp_path);monkeypatch.delenv('ITEM6_WEBHOOK_DIR',raising=False)
    if setup=='private':monkeypatch.setenv('ITEM6_WEBHOOK_DIR','/private')
    if setup=='missing':monkeypatch.setenv('ITEM6_WEBHOOK_DIR',str(tmp_path/'nope'))
    if setup=='nonempty':
        hook=tmp_path/'hook';hook.mkdir();(hook/'webhook-1.json').write_text('{}');monkeypatch.setenv('ITEM6_WEBHOOK_DIR',str(hook))
    with pytest.raises(ValueError,match='webhook folder'):entry.preflight(cfg(),RID,p,data=tmp_path/'pg')


def test_preflight_refuses_a_foreign_database_name_or_missing_dump(tmp_path,monkeypatch):
    p=paths(tmp_path);hook=tmp_path/'hook';hook.mkdir();monkeypatch.setenv('ITEM6_WEBHOOK_DIR',str(hook))
    with pytest.raises(ValueError,match='belong'):entry.preflight({**cfg(),'database_name':'requi_item6_other_run_xx'},RID,p,data=tmp_path/'pg')
    p['dump'].unlink()
    with pytest.raises(ValueError,match='dump'):entry.preflight(cfg(),RID,p,data=tmp_path/'pg')


def test_the_waiver_helpers_never_accept_a_non_typed_reference():
    facts=resume.discover(conn())
    with pytest.raises(AdmissionDenied):
        resume.waive_attempt(object(),facts,authorization_ref='wamid.chat-message-id',dump_sha256='a'*64)


@pytest.mark.skipif(not os.environ.get('ITEM6_TEST_PG_DSN'),reason='needs the restored run database')
def test_discover_against_the_real_restored_run_database():
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(os.environ['ITEM6_TEST_PG_DSN'],row_factory=dict_row) as c:facts=resume.discover(c)
    assert facts['settled_attempts']==174 and facts['attempt_state']=='reserved'
