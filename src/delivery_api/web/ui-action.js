// Shared UI action lifecycle logging: one names-only record at each shared
// point (in-flight guard enter/exit, disabled-state set/clear, Enter key
// resolution) so a stuck-disabled button or a dead Enter key is diagnosable
// from the client log (Debug dialog / everything ZIP) without reproducing it.
// Names only: action ids and resolution outcomes, never field values.
import {clientLog} from './client-core.js';

export function actionBusy(action,button){
  if(button)button.disabled=true;
  clientLog('ui.inflight',`${action} busy`,{action,state:'busy'});
}
export function actionIdle(action,button,outcome){
  if(button)button.disabled=false;
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
