import json,copy,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
from recovery_checkpoint import validate_checkpoint,restore_for_conops
from vmodel_contracts.semantic_review import make_review
from vmodel_contracts import IntentModel

def evidence():
    candidate=json.loads((Path(__file__).parent/'fixtures/revision-source.json').read_text())['intent_model']
    r=make_review(candidate['session_id'],'intent',candidate,[]).model_dump(mode='json')
    resolved=IntentModel.model_validate(candidate).model_dump(mode='json')
    values={'semantic_review':r,'semantic_review_resolved':True,'intent_model':resolved,'messages':['founder transcript'],'submission':{'submission_id':r['session_id']},'verdict':{'accepted':True}}
    return values,r

def test_bound_checkpoint_restores_conops_without_interview_or_approval():
    v,r=evidence();calls=[]
    graph=SimpleNamespace(get_state=lambda cfg:SimpleNamespace(values=v),update_state=lambda cfg,state,**kw:calls.append((state,kw)))
    p=SimpleNamespace(graph=graph,cfg={},session_id=r['session_id'])
    out=restore_for_conops(p,r,actor='founder')
    assert calls==[({},{'as_node':'distill_intent'})]
    assert p.state is None and p.open_input is None and p.result==v
    assert out['next_action']=='regenerate-whole-conops' and out['automatic_approval'] is False

@pytest.mark.parametrize('change',[lambda v:v.update(semantic_review_resolved=False),lambda v:v.update(conops_draft={'partial':'not allowed'}),lambda v:v.update(messages=[]),lambda v:v['intent_model'].update(problem_statement='changed')])
def test_stale_checkpoint_or_review_rejected(change):
    v,r=evidence();change(v)
    with pytest.raises(ValueError):validate_checkpoint(v,r,actor='founder',session_id=r['session_id'])


@pytest.mark.engine_integration
def test_existing_checkpoint_readonly_does_not_run_setup_ddl(tmp_path):
    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver
    path=tmp_path/'checkpoints.sqlite'
    with sqlite3.connect(path) as conn:
        saver=SqliteSaver(conn)
        saver.put({'configurable':{'thread_id':'fixture','checkpoint_ns':''}},
          {'v':1,'id':'fixture-id','ts':'fixture','channel_values':{},'channel_versions':{},'versions_seen':{}}, {}, {})
    before=path.read_bytes()
    with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as conn:
        saver=SqliteSaver(conn);saver.is_setup=True
        assert saver.get_tuple({'configurable':{'thread_id':'fixture'}}) is not None
    assert path.read_bytes()==before


@pytest.mark.engine_integration
def test_wal_checkpoint_read_on_ephemeral_copy_preserves_original(tmp_path):
    import sqlite3,shutil
    from langgraph.checkpoint.sqlite import SqliteSaver
    source=tmp_path/'original';source.mkdir();path=source/'checkpoints.sqlite'
    conn=sqlite3.connect(path)
    conn.execute('PRAGMA wal_autocheckpoint=0')
    saver=SqliteSaver(conn)
    saver.put({'configurable':{'thread_id':'fixture','checkpoint_ns':''}},
      {'v':1,'id':'fixture-id','ts':'fixture','channel_values':{'test':'in-WAL'},'channel_versions':{},'versions_seen':{}}, {}, {})
    original={p.name:p.read_bytes() for p in source.iterdir()}
    destination=tmp_path/'copy';shutil.copytree(source,destination)
    with sqlite3.connect(destination/'checkpoints.sqlite') as copy:
        read=SqliteSaver(copy);read.is_setup=True
        assert read.get_tuple({'configurable':{'thread_id':'fixture'}}).checkpoint['channel_values']=={'test':'in-WAL'}
    assert {p.name:p.read_bytes() for p in source.iterdir()}==original
    conn.close()
