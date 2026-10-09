"""Real PostgreSQL RLS contract tests. No cloud endpoints or models.

Run with DELIVERY_TEST_POSTGRES_DSN pointing to an isolated freshly migrated DB.
Never run against production. Missing Postgres is a skip, not evidence of RLS.
"""
import os
import subprocess
import pytest

DSN = os.getenv('DELIVERY_TEST_POSTGRES_DSN')
if DSN and 'dbname=test_tenant_' not in DSN:
    raise RuntimeError('refusing integration tests outside an isolated test_tenant_ database')
pytestmark = pytest.mark.skipif(not DSN, reason='requires isolated migrated Postgres')
A = '00000000-0000-0000-0000-000000000001'
B = '00000000-0000-0000-0000-000000000002'


def sql(query, owner=A, success=True):
    prefix = 'BEGIN; SET LOCAL ROLE authenticated; '
    if owner is not None:
        prefix += f"SELECT set_config('request.jwt.claim.sub','{owner}',true); SELECT set_config('request.jwt.claim.session_id','{owner}',true); "
    result = subprocess.run(['psql', DSN, '-v', 'ON_ERROR_STOP=1', '-Atq','-f','-'], input=prefix + query + '; ROLLBACK;', text=True,
                            capture_output=True)
    if success:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0, result.stdout
        assert 'row-level security' in result.stderr or 'foreign key' in result.stderr
    return result.stdout.strip().splitlines()


@pytest.fixture(scope='module', autouse=True)
def seed():
    if not DSN: return
    query = f"""
    TRUNCATE tasks,runs CASCADE;
    INSERT INTO tasks(task_id,title,state,created_at,updated_at,owner_id)
    VALUES ('a','Same Name','pending',0,0,'{A}'),('b','Same Name','pending',0,0,'{B}');
    INSERT INTO events(task_id,kind,message,ts) VALUES ('a','private','A',0),('b','private','B',0);
    INSERT INTO runs(run_id,state,budget_usd,created_at,updated_at,owner_id)
    VALUES ('ra','pending',0,0,0,'{A}'),('rb','pending',0,0,0,'{B}');
    INSERT INTO engine_sessions VALUES ('sa','{A}','a',1,0),('sb','{B}','b',1,0);
    """
    result = subprocess.run(['psql', DSN, '-v', 'ON_ERROR_STOP=1', '-qc',query],
                            text=True,capture_output=True)
    assert result.returncode == 0, result.stderr


def test_restricted_role_is_not_bypass():
    assert sql("SELECT rolsuper::text || ':' || rolbypassrls::text FROM pg_roles WHERE rolname=current_user")[-1] == 'false:false'


@pytest.mark.parametrize('owner,visible', [(A,'a'),(B,'b')])
def test_two_users_see_only_own_tasks_and_children(owner, visible):
    assert sql('SELECT task_id FROM tasks',owner)[-1] == visible
    assert sql('SELECT task_id FROM events',owner)[-1] == visible
    assert sql('SELECT task_id FROM engine_sessions',owner)[-1] == visible


def test_missing_identity_denied():
    assert sql('SELECT count(*) FROM tasks',None)[-1] == '0'
    assert sql('SELECT count(*) FROM events',None)[-1] == '0'
    sql(f"INSERT INTO tasks(task_id,title,state,created_at,updated_at,owner_id) VALUES ('z','z','pending',0,0,'{A}')",None,False)


def test_cross_tenant_direct_id_reads_and_writes():
    assert sql("SELECT count(*) FROM tasks WHERE task_id='b'")[-1] == '0'
    assert sql("UPDATE tasks SET title='stolen' WHERE task_id='b' RETURNING task_id") == [A,A]
    assert sql("DELETE FROM tasks WHERE task_id='b' RETURNING task_id") == [A,A]
    sql(f"UPDATE tasks SET owner_id='{B}' WHERE task_id='a'",success=False)


def test_cross_tenant_inserts_and_parent_rebinding():
    sql(f"INSERT INTO tasks(task_id,title,state,created_at,updated_at,owner_id) VALUES ('x','x','pending',0,0,'{B}')",success=False)
    sql("INSERT INTO events(task_id,kind,message,ts) VALUES ('b','private','attack',0)",success=False)
    sql("UPDATE events SET task_id='b' WHERE task_id='a'",success=False)
    sql(f"INSERT INTO engine_sessions VALUES ('evil','{A}','b',1,0)",success=False)
    sql("INSERT INTO artifacts(run_id,kind,ref,ts) VALUES ('rb','raw','private',0)",success=False)
    sql(f"INSERT INTO usage(run_id,record,ts,user_id) VALUES ('rb','{{}}',0,'{A}')",success=False)


def test_every_public_table_has_forced_rls():
    result = subprocess.run(['psql',DSN,'-Atqc',
      "SELECT count(*) FROM pg_tables t JOIN pg_class c ON c.relname=t.tablename JOIN pg_namespace n ON n.oid=c.relnamespace WHERE t.schemaname='public' AND n.nspname='public' AND NOT (c.relrowsecurity AND c.relforcerowsecurity)"],text=True,capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '0'


@pytest.mark.parametrize('table,insert',[
 ('phases',"INSERT INTO phases(task_id,phase_key,label,position) VALUES ('{task}','x','private',0)"),
 ('gates',"INSERT INTO gates(task_id,gate_key,after_phase,label,position) VALUES ('{task}','x','x','private',0)"),
 ('review_comments',"INSERT INTO review_comments(task_id,wait_id,revision,actor,comment,created_at) VALUES ('{task}','w','r','me','private',0)"),
 ('decision_reviews',"INSERT INTO decision_reviews VALUES ('{task}','w','r','private',0)"),
 ('decision_drafts',"INSERT INTO decision_drafts VALUES ('{task}','w','r','d','private',0)"),
 ('progress_ingest',"INSERT INTO progress_ingest VALUES ('{task}','private','s',0)"),
 ('progress_seen',"INSERT INTO progress_seen VALUES ('{task}','private','e')"),
 ('answer_feedback',"INSERT INTO answer_feedback(task_id,input_ref,target,actor,created_at,updated_at) VALUES ('{task}','i','private','me',0,0)"),
 ('task_artifacts',"INSERT INTO task_artifacts(task_id,generation,phase_key,name,ref,created_at) VALUES ('{task}',1,'private','name','private',0)"),
])
def test_every_child_policy_insert_select_update_delete(table,insert):
    sql(insert.format(task='a'))
    sql(insert.format(task='b'),success=False)
    # Stage both tenants' records inside a rolled-back admin transaction, switch
    # into the production non-bypass identity before testing visibility/effects.
    script=f"""BEGIN;
    {insert.format(task='a')}; {insert.format(task='b')};
    SET LOCAL ROLE authenticated;
    SELECT set_config('request.jwt.claim.sub','{A}',true);
    SELECT set_config('request.jwt.claim.session_id','{A}',true);
    SELECT count(*) FROM {table} WHERE task_id='b';
    UPDATE {table} SET task_id=task_id WHERE task_id='b' RETURNING task_id;
    DELETE FROM {table} WHERE task_id='b' RETURNING task_id;
    ROLLBACK;"""
    result=subprocess.run(['psql',DSN,'-v','ON_ERROR_STOP=1','-Atq','-f','-'],input=script,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip().splitlines()==[A,A,'0']


def test_signup_profile_quota_not_metadata_or_client_controlled():
    script="""BEGIN;
    INSERT INTO auth.users VALUES ('00000000-0000-0000-0000-000000000099');
    SELECT token_quota FROM public.users WHERE user_id='00000000-0000-0000-0000-000000000099';
    ROLLBACK;"""
    result=subprocess.run(['psql',DSN,'-v','ON_ERROR_STOP=1','-Atq','-f','-'],input=script,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='0'


def test_supabase_native_json_claims_session_check():
    # PostgREST sets a claims JSON object, unlike our transaction-local hook.
    import json
    claims=json.dumps({'sub':A,'session_id':A})
    result=subprocess.run(['psql',DSN,'-v','ON_ERROR_STOP=1','-Atq','-f','-'],
      input=f"BEGIN; SET LOCAL ROLE authenticated; SELECT set_config('request.jwt.claim.sub','{A}',true); SELECT set_config('request.jwt.claims','{claims}',true); SELECT public.requi_session_active(); ROLLBACK;",
      text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip().splitlines()[-1]=='t'


def test_engine_session_registry_update_not_user_authorized():
    result=subprocess.run(['psql',DSN,'-v','ON_ERROR_STOP=1','-Atq','-f','-'],
      input=f"BEGIN; SET LOCAL ROLE authenticated; SELECT set_config('request.jwt.claim.sub','{A}',true); SELECT set_config('request.jwt.claim.session_id','{A}',true); UPDATE engine_sessions SET task_id='b' WHERE session_id='sa'; ROLLBACK;",
      text=True,capture_output=True)
    assert result.returncode!=0
    assert 'permission denied' in result.stderr


@pytest.mark.parametrize('table,insert',[
 ('phases',"INSERT INTO phases(task_id,phase_key,label,position) VALUES ('{task}','x','private',0)"),
 ('gates',"INSERT INTO gates(task_id,gate_key,after_phase,label,position) VALUES ('{task}','x','x','private',0)"),
 ('review_comments',"INSERT INTO review_comments(task_id,wait_id,revision,actor,comment,created_at) VALUES ('{task}','w','r','me','private',0)"),
 ('decision_reviews',"INSERT INTO decision_reviews VALUES ('{task}','w','r','private',0)"),
 ('decision_drafts',"INSERT INTO decision_drafts VALUES ('{task}','w','r','d','private',0)"),
 ('progress_ingest',"INSERT INTO progress_ingest VALUES ('{task}','private','s',0)"),
 ('progress_seen',"INSERT INTO progress_seen VALUES ('{task}','private','e')"),
 ('answer_feedback',"INSERT INTO answer_feedback(task_id,input_ref,target,actor,created_at,updated_at) VALUES ('{task}','i','private','me',0,0)"),
 ('task_artifacts',"INSERT INTO task_artifacts(task_id,generation,phase_key,name,ref,created_at) VALUES ('{task}',1,'private','name','private',0)"),
])
def test_every_child_policy_insert_select_update_delete(table,insert):
    sql(insert.format(task='a'))
    sql(insert.format(task='b'),success=False)
    # Stage both tenants' records inside a rolled-back admin transaction, switch
    # into the production non-bypass identity before testing visibility/effects.
    script=f"""BEGIN;
    {insert.format(task='a')}; {insert.format(task='b')};
    SET LOCAL ROLE authenticated;
    SELECT set_config('request.jwt.claim.sub','{A}',true);
    SELECT set_config('request.jwt.claim.session_id','{A}',true);
    SELECT count(*) FROM {table} WHERE task_id='b';
    UPDATE {table} SET task_id=task_id WHERE task_id='b' RETURNING task_id;
    DELETE FROM {table} WHERE task_id='b' RETURNING task_id;
    ROLLBACK;"""
    result=subprocess.run(['psql',DSN,'-v','ON_ERROR_STOP=1','-Atq','-f','-'],input=script,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip().splitlines()==[A,A,'0']


def test_signup_profile_quota_not_metadata_or_client_controlled():
    script="""BEGIN;
    INSERT INTO auth.users VALUES ('00000000-0000-0000-0000-000000000099');
    SELECT token_quota FROM public.users WHERE user_id='00000000-0000-0000-0000-000000000099';
    ROLLBACK;"""
    result=subprocess.run(['psql',DSN,'-v','ON_ERROR_STOP=1','-Atq','-f','-'],input=script,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='0'
