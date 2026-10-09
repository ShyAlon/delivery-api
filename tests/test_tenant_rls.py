

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
