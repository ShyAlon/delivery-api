// Shared UI action lifecycle logging: one names-only record at each shared
// point (in-flight guard enter/exit, disabled-state set/clear, Enter key
// resolution) so a stuck-disabled button or a dead Enter key is diagnosable
// from the client log (Debug dialog / everything ZIP) without reproducing it.
// Names only: action ids and resolution outcomes, never field values.
import {clientLog} from './client-core.js';

// Log-only: these helpers record state transitions. The DOM effect lives
// exactly once, in runAction - a helper that both logs and toggles let a
// settle path re-enable a button behind the guard's back.
export function actionBusy(action,button){
  clientLog('ui.inflight',`${action} busy`,{action,state:'busy'});
}
export function actionIdle(action,button,outcome){
  clientLog('ui.inflight',`${action} idle (${outcome})`,{action,state:'idle',outcome});
}
export function guardBlocked(action,reason){
  clientLog('ui.inflight',`${action} blocked: ${reason}`,{action,state:'blocked',reason});
}
export function disabledSet(action,target){
  clientLog('ui.disabled',`${action} disabled ${target}`,{action,target,state:'disabled'});
}
export function disabledClear(action,target){
  clientLog('ui.disabled',`${action} enabled ${target}`,{action,target,state:'enabled'});
}
// Enter pressed: log which handler the key resolved to, or why none ran.
export function logEnter(context,resolution){
  clientLog('ui.enter',`${context}: Enter -> ${resolution}`,{context,resolution});
}

// The one in-flight guard for server-call actions: disables the triggering
// button(s) with a visible reason BEFORE the first await, blocks re-entry for
// the action while in flight, and restores the buttons when the call returns.
// Every server-call button routes through this - no per-page copies.
const inflight=new Set();
export async function runAction(action,buttons,fn,{busyLabel=null,reason='Working on it - one moment',keepDisabledOnSuccess=false}={}){
  const list=(Array.isArray(buttons)?buttons:[buttons]).filter(Boolean);
  if(inflight.has(action)){guardBlocked(action,'in-flight');return undefined}
  inflight.add(action);
  const saved=list.map(b=>({b,text:b.textContent,title:b.title||'',wasDisabled:b.disabled}));
  list.forEach(b=>{b.disabled=true;b.classList.add('is-busy');b.setAttribute('aria-busy','true');b.title=reason;if(busyLabel)b.textContent=busyLabel});
  actionBusy(action,list[0]);
  let ok=false;
  try{
    const out=await fn();
    ok=true;
    actionIdle(action,list[0],'success');
    return out;
  }catch(error){
    actionIdle(action,list[0],'error');
    throw error;
  }finally{
    inflight.delete(action);
    if(!(ok&&keepDisabledOnSuccess))saved.forEach(({b,text,title,wasDisabled})=>{b.disabled=wasDisabled;b.classList.remove('is-busy');b.removeAttribute('aria-busy');b.textContent=text;b.title=title});
  }
}
