"""Actual InterviewApplication with hermetic models and attended typed responses."""
import json
from langchain_core.messages import AIMessage
from vmodel_contracts.interactive import InputResponse,InputKind
from interview_engine.application import InterviewApplication
from interview_engine.stop_fence import StopFence


def configure_interview_models(monkeypatch):
 from interview_engine import model_config as mc
 monkeypatch.setenv('INTERVIEW_ENGINE_CATEGORIZERS','rules')
 monkeypatch.setattr(mc,'_DOTENV',{});monkeypatch.setattr(mc,'_CALLS_USED',0)
 needs={'stakeholder_needs':[{'need_id':'SN-1','statement':'founders need specs','kind':'functional','source_turns':[0]}]}
 scope={'problem_statement':'p','target_users':['u'],'core_value_proposition':'v','in_scope':['a'],'out_of_scope':['b'],'source_turns':{}}
 conops=[{'operating_environment':'phone, often offline'},{'trigger':'habit completed'},
  {'operational_flow':['open product','record points']},{'exceptions':['offline']},
  {'name':'Native app','summary':'installed app'},{'name':'PWA','summary':'installable web product'},
  {'advantages':['native APIs']},{'disadvantages':['store release']},{'advantages':['one deployment']},{'disadvantages':['platform limits']}]
 class Fake:
  def __init__(self,replies):self.replies=list(replies)
  def invoke(self,messages):
   if not self.replies:raise ValueError('unexpected fixture call: '+str(messages[-1].content)[:180])
   value=self.replies.pop(0)
   return AIMessage(content=value if isinstance(value,str) else json.dumps(value))
 models={'probing':Fake(['[INTERVIEW COMPLETE]']),'distillation':Fake([needs,scope,[]]),'conops':Fake(conops),
  'scope_tiebreak':Fake([{'is_web_app':True,'archetype':'saas_crud','confidence':.9,'reason':'web app'}])}
 monkeypatch.setattr(mc,'build_chat_model',lambda stage,**kw:models[stage])
 return models


def test_actual_interview_outputs_under_owned_namespace(tmp_path,monkeypatch):
 configure_interview_models(monkeypatch)
 owner='00000000-0000-0000-0000-000000000001';sid='test-interview';base=tmp_path/'tenants'/owner/'sessions'
 fence=StopFence(base/sid/'interview/stop-request.json')
 with fence.running():
  app=InterviewApplication(sid,'test-task','a saas web app for surf schools, with booking and customer logins',artifact_root=str(base))
  request=app.advance();inputs=[]
  for _ in range(8):
   if request is None:break
   inputs.append(request.kind)
   if request.kind==InputKind.CHOICE:response=InputResponse(input_id=request.input_id,actor_id=owner,value=1,rationale='reliable native APIs')
   elif request.kind==InputKind.APPROVAL:response=InputResponse(input_id=request.input_id,actor_id=owner,value=1)
   else:raise ValueError('unhandled real input '+request.prompt)
   request=app.advance(response)
  assert app.finished
 from vmodel_contracts import IntentModel,ConOpsDocument
 root=base/sid/'interview'
 IntentModel.model_validate_json((root/'intent_model.json').read_bytes());ConOpsDocument.model_validate_json((root/'conops.json').read_bytes())
 assert InputKind.CHOICE in inputs and InputKind.APPROVAL in inputs
 app.checkpointer.conn.close()
