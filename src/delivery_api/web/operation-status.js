/* One operation lifecycle and accessible status view for both applications.
   Labels are UI-owned descriptions, never request bodies, URLs or error prose. */
const active=new Map(),listeners=new Set();let sequence=0,last=null,actionRevision=0;
const notify=()=>listeners.forEach(fn=>fn(snapshot()));
export function snapshot(){
 const items=[...active.values()],actions=items.filter(x=>x.kind==='action');
 const current=actions.at(-1)||items.at(-1)||last;
 return {state:items.length?'busy':current?.state||'idle',text:current?.text||'Ready',pending:items.length,requests:items.filter(x=>x.kind==='request').length};
}
export function beginOperation(label,{kind='request',success='Server response received',failure='Request failed; check the page before retrying'}={}){
 const id=++sequence,revision=actionRevision,parent=kind==='request'?[...active.values()].filter(x=>x.kind==='action').at(-1):null;if(kind==='action')actionRevision++;
 active.set(id,{text:label,kind,state:'busy',failed:false});notify();let ended=false;
 return outcome=>{
  if(ended)return;ended=true;const own=active.get(id);active.delete(id);
  // Bind a request failure at start, not completion: old background reads
  // must never poison actions that started while that read was pending.
  if(outcome!=='success'&&parent)parent.failed=true;
  if(own?.failed)outcome='error';
  // Background reads cannot erase a finished action's success/error message.
  if(kind==='action'||(outcome!=='success'&&!parent)||(!last?.action&&revision===actionRevision))last={text:outcome==='success'?success:failure,state:outcome==='success'?'success':'error',action:kind==='action'};
  notify();
 };
}
export function requestDescription(url,options={}){
 const path=new URL(url,typeof location==='undefined'?'http://fixture':location.origin).pathname;
 const auth={'/api/auth':'Checking sign-in','/auth/signin':'Signing in','/auth/signup':'Creating account','/auth/logout':'Signing out','/auth/refresh':'Renewing sign-in','/auth/email-availability':'Checking email availability'};
 if(auth[path])return auth[path];
 return options.method&&options.method!=='GET'?'Sending action to server':'Reading server data';
}
export function mountOperationStatus(host){
 const existing=host.querySelector('[data-operation-status]');if(existing)return existing;
 if(!document.getElementById('operation-status-style')){const style=document.createElement('style');style.id='operation-status-style';style.textContent='.operation-status{display:flex;align-items:center;gap:8px;min-width:0;font:18px/1.5 Manrope,system-ui,sans-serif}.operation-status .operation-text{overflow-wrap:anywhere}.operation-status[data-state=error]{color:#a21f19}.operation-status[data-state=success]{color:#234e2a}.operation-status .operation-mark{flex:none}.operation-status[data-state=busy] .operation-mark{animation:operation-pulse 1.2s ease-in-out infinite}@keyframes operation-pulse{50%{opacity:.35}}@media(prefers-reduced-motion:reduce){.operation-status[data-state=busy] .operation-mark{animation:none}}';document.head.append(style);}
 const view=document.createElement('div');view.className='operation-status';view.dataset.operationStatus='';view.setAttribute('role','status');view.setAttribute('aria-live','polite');view.setAttribute('aria-atomic','true');
 const mark=document.createElement('span');mark.className='operation-mark';mark.setAttribute('aria-hidden','true');const text=document.createElement('span');text.className='operation-text';view.append(mark,text);host.append(view);
 const draw=value=>{view.dataset.state=value.state;view.setAttribute('aria-busy',String(value.state==='busy'));mark.textContent=value.state==='busy'?'◌':value.state==='error'?'!':value.state==='success'?'✓':'·';text.textContent=value.text+(value.requests>1?' ('+value.requests+' server requests in progress)':'');};
 listeners.add(draw);host.addEventListener('close',()=>listeners.delete(draw),{once:true});draw(snapshot());return view;
}
