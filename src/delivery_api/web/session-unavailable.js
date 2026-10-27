// A task whose engine session is not found in this environment: the control snapshot says failed, no process is live, and the engine answered 404 code 120.
// Snapshot state is local evidence only. Ownership guidance does not promise another environment is live.
export function sessionUnavailable(control){
  const d=control?.diagnostic;
  return Boolean(control&&control.mode==='failed'&&!control.process_live&&d&&Number(d.http_status)===404&&Number(d.code)===120);
}
function when(ts){return new Date(Number(ts)*1000).toLocaleString([],{year:'numeric',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',timeZoneName:'short'})}
export function lastRecorded(events){
  const last=[...(events||[])].reverse().find(e=>Number(e?.ts)>0);
  if(!last)return 'Session unavailable in this environment; no active process reported; no progress is recorded. Open the environment that owns this session (usually the original deployment) to check continuation. Account actions here can still change live data.';
  const stamp=when(last.ts);
  return last.kind==='waiting_input'?`Last recorded state: waiting for your answer at ${stamp}; session unavailable in this environment; no active process reported. Open the environment that owns this session (usually the original deployment) to check continuation. Account actions here can still change live data.`:
    `Session unavailable in this environment; no active process reported; last recorded progress ${stamp}. Open the environment that owns this session (usually the original deployment) to check continuation. Account actions here can still change live data.`;
}
