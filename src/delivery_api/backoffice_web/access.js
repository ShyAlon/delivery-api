// Private workspace only. The service still rechecks authority on every call.
const panels={
 'accounts-panel':['analytics.read'],'global-sessions-panel':['sessions.read'],
 'expense-panel':['analytics.read'],'releases-panel':['logs.read'],'vendor-panel':['analytics.read'],
 'model-pins-panel':['services.operate'],'payments-panel':['payments.read'],
 'audit-panel':['logs.read','debug.read'],'active-jobs-panel':['debug.read']
};
export async function accessBoundary(request){
 const states=new Map();
 for(const id of Object.keys(panels)){
  const panel=document.getElementById(id);if(!panel)continue;
  const note=document.createElement('div');note.className='note access-denied';note.setAttribute('role','status');panel.prepend(note);
  states.set(id,{panel,note,saved:new Map()});
 }
 async function check(){
  let caps=null,status=null,details={};
  for(const state of states.values())state.observer?.disconnect();
  for(const {note} of states.values())note.textContent='Checking current service access...';
  try{const response=await request('/api/operations/access',{credentials:'same-origin',cache:'no-store'});status=response.status;
   if(response.ok){const data=await response.json();if(data.capabilities&&typeof data.capabilities==='object'){caps=data.capabilities;details=data.checks||{}}}
  }catch{}
  for(const [id,{panel,note,saved}] of states){
   const missing=panels[id].filter(cap=>caps?.[cap]!==true);
   const known=caps!==null&&missing.every(cap=>caps[cap]===false);
   for(const [control,old] of saved){if(control.isConnected){control.disabled=old.disabled;control.title=old.title}}saved.clear();
   note.replaceChildren();
   if(caps!==null&&!missing.length){note.remove();continue;}
   if(!note.isConnected)panel.prepend(note);
   const reasons=missing.map(cap=>details[cap]?.reason).filter(Boolean);
   const message=known?'Service access denied for '+missing.join(', ')+'. '+(reasons.join(' ')||'The current delivery process and database registry did not both authorize this session.'): 'Access could not be checked'+(status?' (HTTP '+status+')':'')+'. This is not proof of a missing capability. Existing rows may be stale.';
   const text=document.createElement('p');text.textContent=message;note.append(text);
   const next=document.createElement('p');next.textContent=known?'Review req backoffice-access --test-owner first. If protected settings were updated after this delivery process started, restart with req preview --apply; that restarts execution services, not just this page. Do not add permissions blindly.':'Retry this access check. If it still fails, inspect Back office Debug request evidence. No service restart or grant change is proven necessary.';note.append(next);
   const disable=()=>panel.querySelectorAll('button,input,select,textarea').forEach(control=>{if(note.contains(control))return;if(!saved.has(control))saved.set(control,{disabled:control.disabled,title:control.title});if(!control.disabled)control.disabled=true;if(control.title!==message)control.title=message});
   disable();const observer=new MutationObserver(disable);observer.observe(panel,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled']});states.get(id).observer=observer;
   const retry=document.createElement('button');retry.textContent='Retry access check';retry.onclick=()=>check();note.append(retry);
  }
  return caps||{};
 }
 return check();
}
