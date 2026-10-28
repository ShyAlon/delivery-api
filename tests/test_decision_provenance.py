from delivery_api import app as module


def test_architecture_gate_labels_model_choice_without_founder_source(tmp_path,monkeypatch,pg_scoped_workspace):
    sid='provenance-'+pg_scoped_workspace.scope.owner_id
    root=tmp_path/'artifacts'/sid/'interview';root.mkdir(parents=True)
    store=pg_scoped_workspace.store;tid=store.create_task('Architecture','brief')['task_id']
    store.set_engine_session(tid,sid)
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path/'artifacts'));monkeypatch.setattr(module,'store',store)
    rows=module._decision_provenance(tid,{'decisions':[{'decision_id':'ADR-4','title':'Choose SQLite',
        'decision':'Use SQLite','baseline_refs':[],'technology_selections':[{'slot_id':'slot:database','display_name':'SQLite'}]}]})
    assert rows[0]['label']=='Model-proposed decision' and rows[0]['founder_evidence']==[]


def test_architecture_gate_labels_exact_grounded_founder_preference(tmp_path,monkeypatch,pg_scoped_workspace):
    import json
    sid='provenance-'+pg_scoped_workspace.scope.owner_id
    root=tmp_path/'artifacts'/sid/'interview';root.mkdir(parents=True)
    store=pg_scoped_workspace.store;tid=store.create_task('Architecture','brief')['task_id']
    store.set_engine_session(tid,sid)
    (root/'intent_model.json').write_text(json.dumps({'session_id':sid,'problem_statement':'Progress',
        'target_users':['Founder'],'core_value_proposition':'Visibility',
        'stakeholder_needs':[{'need_id':'SN-1','statement':'See progress','kind':'functional','source_turns':[2]}],
        'in_scope':['View progress'],'out_of_scope':[],
        'tech_preferences':[{'area':'database','preference':'SQLite','strength':'preferred','source_turns':[4]}],
        'source_turns':{'problem_statement':[2]}}))
    monkeypatch.setenv('VMODEL_ARTIFACTS_DIR',str(tmp_path/'artifacts'));monkeypatch.setattr(module,'store',store)
    rows=module._decision_provenance(tid,{'decisions':[{'decision_id':'ADR-4','title':'Choose SQLite',
        'baseline_refs':[],'technology_selections':[{'slot_id':'slot:database','display_name':'SQLite'}]}]})
    assert rows[0]['label']=='Founder-sourced preference' and rows[0]['founder_evidence'][0]['source_turns']==[4]
