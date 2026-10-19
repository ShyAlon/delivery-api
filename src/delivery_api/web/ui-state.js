const schema=await fetch('/assets/ui-modes.json',{cache:'no-store'}).then(response=>{
  if(!response.ok)throw new Error(`UI mode schema unavailable (${response.status})`);
  return response.json();
});

export const TASK_UI_MODES=new Set(Object.keys(schema));
export const TASK_UI_ACTIONS=Object.fromEntries(
  Object.entries(schema).map(([mode,actions])=>[mode,Object.fromEntries(actions.map(action=>[action,true]))])
);

export function reduceTaskSnapshot(task){
  const control=task?.control_snapshot;
  if(!control || control.version!==2)throw new Error('Current task control snapshot is unavailable');
  const mapped=control.mode==='waiting_input'?(control.kind==='text'?'engine_question':'engine_decision'):
    control.mode==='waiting_gate'?'phase_gate':control.mode==='working'?'executing':
    control.mode==='ready'?'ready_next':control.mode==='completed'?'complete':control.mode==='failed'?'error':
    control.mode==='not_started'?'not_started':control.mode==='stop_requested'?'stop_requested':control.mode==='stopped'?'stopped':null;
  const mode=TASK_UI_MODES.has(mapped)?mapped:'error',allowed=TASK_UI_ACTIONS[mode];
  const decision=allowed.decision&&control.decision&&(control.decision.options?.length||control.kind==='semantic_review')?control.decision:null;
  const gate=allowed.gate&&control.gate?control.gate:null;
  return Object.freeze({mode,active_phase:control.phase_key,process_live:control.process_live,decision,gate,
    question:control.prompt||null,input_id:control.input_id||control.wait_id||null,
    show_start:Boolean(allowed.start),show_next_phase:Boolean(allowed.next_phase)&&control.kind==='next_phase',show_progress:Boolean(allowed.progress),show_engine_decision:Boolean(decision),show_question:Boolean(allowed.question),
    show_answer_composer:Boolean(allowed.answer),show_phase_gate:Boolean(gate),show_artifacts:Boolean(allowed.artifacts),show_console:mode!=='not_started'&&mode!=='complete'&&mode!=='stop_requested'&&mode!=='stopped'});
}
