// Private workspace only. The service still rechecks authority on every call.
const panels={
 'accounts-panel':['analytics.read'],'global-sessions-panel':['sessions.read'],
 'expense-panel':['analytics.read'],'releases-panel':['logs.read'],'vendor-panel':['analytics.read'],
 'model-pins-panel':['services.operate'],'payments-panel':['payments.read'],
 'audit-panel':['logs.read','debug.read'],'active-jobs-panel':['debug.read']
};
export async function accessBoundary(request){
 let caps={};
 try{const r=await request('/api/operations/access',{credentials:'same-origin',cache:'no-store'});if(r.ok)caps=(await r.json()).capabilities||{}}catch{}
 for(const [id,required] of Object.entries(panels)){
  const panel=document.getElementById(id),missing=required.filter(c=>caps[c]!==true);
  if(!panel||!missing.length)continue;
  const note=document.createElement('p');note.className='note access-denied';note.setAttribute('role','note');note.textContent='Missing capability: '+missing.join(', ')+'. Owner review and restart required.';panel.prepend(note);
  const disable=()=>panel.querySelectorAll('button,input,select,textarea').forEach(b=>{if(!b.disabled)b.disabled=true;if(b.title!==note.textContent)b.title=note.textContent});
  disable();new MutationObserver(disable).observe(panel,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled']});
 }
 return caps;
}
