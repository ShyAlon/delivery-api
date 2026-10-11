"""Actual checkpoint restore -> CONOPS -> fresh reviews. Fixture models only."""
import sys,json
from pathlib import Path
import pytest
pytest.importorskip('interview_engine.application')
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
from recovery_checkpoint import restore_for_conops
from test_item6_interview import configure_interview_models
from interview_engine.application import InterviewApplication
from interview_engine.stop_fence import StopFence
from langchain_core.messages import HumanMessage
from vmodel_contracts.semantic_review import make_review
from vmodel_contracts import IntentModel
from vmodel_contracts.interactive import InputResponse,InputKind
pytestmark=pytest.mark.engine_integration


def test_real_graph_checkpoint_binding_new_conops_review_not_interview(tmp_path,monkeypatch):
    models=configure_interview_models(monkeypatch)
    owner='00000000-0000-0000-0000-000000000001';sid='s'
    candidate=json.loads((Path(__file__).parent/'fixtures/revision-source.json').read_text())['intent_model']
    candidate['session_id']=sid
    review=make_review(sid,'intent',candidate,[]).model_dump(mode='json')
    resolved=IntentModel.model_validate(candidate).model_dump(mode='json')
    fence=StopFence(tmp_path/sid/'interview/stop-request.json')
    with fence.running():
        original=InterviewApplication(sid,'task','original brief',artifact_root=str(tmp_path))
        original.graph.update_state(original.cfg,{'intent_model':resolved,'semantic_review':review,'semantic_review_resolved':True,
            'messages':[HumanMessage(content='founder source')],'submission':{'submission_id':sid},'verdict':{'accepted':True}},as_node='distill_intent')
        original.checkpointer.conn.close()
        program=InterviewApplication(sid,'task','original brief',artifact_root=str(tmp_path))
        restore_for_conops(program,review,actor=owner)
        pending=program.advance()
        assert pending.context['decision_purpose']=='conops_concept'
        assert models['probing'].replies==['[INTERVIEW COMPLETE]']
        assert models['distillation'].replies
        assert pending.input_id is not None
        program.checkpointer.conn.close()
