const schema=await fetch('/assets/ui-modes.json',{cache:'no-store'}).then(response=>{
  if(!response.ok)throw new Error(`UI mode schema unavailable (${response.status})`);
  return response.json();
});

export const TASK_UI_MODES=new Set(Object.keys(schema));
export const TASK_UI_ACTIONS=Object.fromEntries(
  Object.entries(schema).map(([mode,actions])=>[mode,Object.fromEntries(actions.map(action=>[action,true]))])
);

export function reduceTaskSnapshot(task){
  // Delivery's uniform control projection chooses the waiting/working mode.
  // Legacy ui_state supplies the display payload during the additive rollout.
  const source=task?.ui_state||{},control=task?.control_snapshot;
  const mapped=control?.mode==='waiting_input'?(control.kind==='text'?'engine_question':'engine_decision'):
    control?.mode==='waiting_gate'?'phase_gate':control?.mode==='working'?'executing':
    control?.mode==='completed'?'complete':control?.mode==='failed'?'error':
    control?.mode==='not_started'?'not_started':source.mode;
  const mode=TASK_UI_MODES.has(mapped)?mapped:'error',allowed=TASK_UI_ACTIONS[mode];
  const decision=allowed.decision&&source.decision?.options?.length?source.decision:null;
  const gate=allowed.gate&&source.gate?source.gate:null;
  return Object.freeze({mode,active_phase:source.active_phase||task?.active_phase||null,process_live:Boolean(source.process_live),decision,gate,question:source.question||null,input_id:source.input_id||null,
    show_start:Boolean(allowed.start),show_progress:Boolean(allowed.progress),show_engine_decision:Boolean(decision),show_question:Boolean(allowed.question),
    show_answer_composer:Boolean(allowed.answer),show_phase_gate:Boolean(gate),show_artifacts:Boolean(allowed.artifacts),show_console:mode!=='not_started'&&mode!=='complete'});
}
