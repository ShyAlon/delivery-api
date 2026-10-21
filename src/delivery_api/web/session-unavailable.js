// A task whose engine session is not found in this environment: the control snapshot says failed, no process is live, and the engine answered 404 code 120.
// The words say only what that snapshot shows. They do not say anything about other environments or what can be done.
export function sessionUnavailable(control){
  const d=control?.diagnostic;
  return Boolean(control&&control.mode==='failed'&&!control.process_live&&d&&Number(d.http_status)===404&&Number(d.code)===120);
}
function when(ts){return new Date(Number(ts)*1000).toLocaleString([],{year:'numeric',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',timeZoneName:'short'})}
export function lastRecorded(events){
  const last=[...(events||[])].reverse().find(e=>Number(e?.ts)>0);
  if(!last)return 'Session unavailable in this environment; no active process reported; no progress is recorded.';
  const stamp=when(last.ts);
  return last.kind==='waiting_input'?`Last recorded state: waiting for your answer at ${stamp}; session unavailable in this environment; no active process reported.`:
    `Session unavailable in this environment; no active process reported; last recorded progress ${stamp}.`;
}
