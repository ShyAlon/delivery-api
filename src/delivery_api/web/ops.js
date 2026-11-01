import {runAction} from './ui-action.js';
import {el,makeTable} from './ops-table.js';
import {onPageExit} from './page-lifecycle.js';
import {reveal} from './client-core.js';
// The operator views mount inside the app outlet like any other page. mountOpsPage returns nothing;
// it registers its cleanup (timers, listeners, body flags) so leaving /ops tears the page down.
export function mountOpsPage(root,initialView){
'use strict';
let dead=false;
onPageExit(()=>{dead=true;document.body.classList.remove('ops-page');delete document.body.dataset.view});
document.body.classList.add('ops-page');
const $=s=>root.querySelector(s);
const dur=s=>{s=Math.max(0,Math.floor(s));const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);return d?`${d}d ${h}h ${m}m`:h?`${h}h ${m}m`:m?`${m}m ${s%60}s`:`${s}s`};
const localTime=text=>{const t=new Date(String(text||'').replace(' UTC','Z').replace(' ','T'));return isNaN(t)?String(text||''):t.toLocaleString(undefined,{dateStyle:'medium',timeStyle:'long'})};
const num=(v,u)=>v==null?'Not Available':(typeof v==='number'?(Math.round(v*10)/10).toLocaleString():v)+(u||'');

const sessions=makeTable($('#p-sessions'),'Signed-In Users','People who made a request in the last 15 minutes. Visible to administrators only.',[
  {key:'user',label:'User',tip:'The email the person signed in with.'},
  {key:'seconds_ago',label:'Last Seen',tip:'How long ago this person last made a request.',num:true,fmt:v=>dur(v)+' ago'},
  {key:'running',label:'Running',tip:'The run this person has going right now, by phase. None means no run.'}],
  'Nobody has been seen in the last 15 minutes.');
const running=makeTable($('#p-running'),'Running Now','Phases that are using a slot on this server right now.',[
  {key:'run',label:'Run',tip:'A running phase. Names are numbered, not task ids.'},
  {key:'state',label:'State',tip:'Running, Waiting For Memory, Stopping after a stop request, or Past Time Limit.'},
  {key:'seconds',label:'Running For',tip:'How long this phase has held its slot.',num:true,fmt:v=>dur(v)},
  {key:'peak_memory_mb',label:'Peak Memory',tip:'Largest memory this phase has used so far, in megabytes.',num:true,fmt:v=>num(v,' MB')}],
  'Nothing is running right now.');
const recent=makeTable($('#p-recent'),'Recent Runs','The latest finished and running phases with their CPU and memory.',[
  {key:'phase',label:'Phase',tip:'Which phase of the build ran.'},
  {key:'started',label:'Started (UTC)',tip:'When the run was first sampled, as month-day and time in UTC.',fmt:v=>v.slice(5,16)},
  {key:'ended',label:'Last Sample (UTC)',tip:'When the run was last sampled, as month-day and time in UTC.',fmt:v=>v.slice(5,16)},
  {key:'avg_cpu_percent',label:'Average CPU',tip:'Average CPU use as a percent of one core.',num:true,fmt:v=>num(v,'%')},
  {key:'max_cpu_percent',label:'Peak CPU',tip:'Highest CPU use as a percent of one core.',num:true,fmt:v=>num(v,'%')},
  {key:'cpu_seconds',label:'CPU Seconds',tip:'Total CPU time the run used. Compute credits are charged on this.',num:true,fmt:v=>num(v)},
  {key:'peak_memory_mb',label:'Peak Memory',tip:'Largest memory the run used, in megabytes.',num:true,fmt:v=>num(v,' MB')}],
  'No run has been measured yet.');

const stops=makeTable($('#stops-view'),'Admin Stops','Every time a run was stopped, newest first. Operator stops name the operator.',[
  {key:'when',label:'When (UTC)',tip:'When the stop was recorded.'},
  {key:'stopped_by',label:'Stopped By',tip:'Owner means the person who started the run. Operator means an administrator. Server (Memory) means the server stopped the biggest run because memory was nearly full.'},
  {key:'operator',label:'Operator',tip:'Which administrator stopped it, when an operator did.',empty:'Not Applicable'},
  {key:'owner',label:'Owner',tip:'Whose run it was.'},
  {key:'phase',label:'Phase',tip:'Which phase was running. Older stops did not record it.',empty:'Not Recorded',fmt:v=>v.charAt(0).toUpperCase()+v.slice(1).replace(/_/g,' ')},
  {key:'peak_memory_mb',label:'Peak Memory',tip:'Largest memory the run had used when it was stopped, in megabytes.',num:true,fmt:v=>num(v,' MB')}],
  'No run has been stopped yet.');
const stopsBox=el('div',{className:'empty',hidden:true});
$('#stops-view').append(stopsBox);
async function loadStops(){
  try{
    const r=await fetch('/api/admin/operations/stops',{credentials:'same-origin'});
    if(r.status===401||r.status===403){stopsBox.hidden=false;stopsBox.textContent='This page is for administrators.';stops.set([]);return}
    if(r.status===503){const b=await r.json().catch(()=>({}));const d=b.detail||{};stopsBox.hidden=false;stopsBox.textContent=(d.error||'The stop list is not available.')+' '+(d.next||'');stops.set([]);return}
    if(!r.ok)throw new Error(String(r.status));
    stopsBox.hidden=true;stops.set((await r.json()).stops);
  }catch(e){stopsBox.hidden=false;stopsBox.textContent='Could not read the stop list ('+e.message+'). Press Refresh to try again.';stops.set([])}
}
const spend=makeTable($('#spend-view'),'Credits And Spend','Every account: credits left, credits used and what the model provider charged us. Operators only.',[
  {key:'account',label:'Account',tip:'The account holder.'},
  {key:'balance_credits',label:'Credit Balance',tip:'Credits the account has left.',num:true,fmt:v=>num(v)},
  {key:'credits_24h',label:'Credits Used (24h)',tip:'Credits spent in the last 24 hours, including compute. History starts when the credit history was switched on.',num:true,fmt:v=>num(v)},
  {key:'credits_7d',label:'Credits Used (7d)',tip:'Credits spent in the last 7 days.',num:true,fmt:v=>num(v)},
  {key:'expense_24h_usd',label:'Vendor Expense (24h)',tip:'What the model provider charged us for this account in the last 24 hours, in US dollars.',num:true,fmt:v=>'$'+Number(v).toFixed(2)},
  {key:'expense_7d_usd',label:'Vendor Expense (7d)',tip:'Provider charges for this account in the last 7 days, in US dollars.',num:true,fmt:v=>'$'+Number(v).toFixed(2)}],
  'No accounts yet.');
const spendBox=el('div',{className:'empty',hidden:true});
const spendCards=el('div',{id:'spend-cards'});
$('#spend-view').prepend(spendCards);$('#spend-view').append(spendBox);
async function loadSpend(){
  const fail=m=>{spendBox.hidden=false;spendBox.textContent=m;spend.set([]);spendCards.replaceChildren()};
  try{
    const r=await fetch('/api/admin/operations/spend',{credentials:'same-origin'});
    if(r.status===401||r.status===403)return fail('This page is for administrators.');
    if(r.status===503){const b=await r.json().catch(()=>({}));const d=b.detail||{};return fail((d.error||'The credits page is not available.')+' '+(d.next||''))}
    if(!r.ok)throw new Error(String(r.status));
    const d=await r.json(),sum=k=>d.accounts.reduce((s,x)=>s+x[k],0);
    spendBox.hidden=true;spend.set(d.accounts);
    spendCards.replaceChildren(card('Accounts',String(d.accounts.length),d.truncated?'First 500 shown':'','How many accounts have credits.'),
      card('Credits Used (24h)',num(sum('credits_24h')),'All accounts','Credits spent by everyone in the last 24 hours.'),
      card('Vendor Expense (24h)','$'+sum('expense_24h_usd').toFixed(2),'All accounts','Model provider charges in the last 24 hours.'),
      card('Vendor Expense (7d)','$'+sum('expense_7d_usd').toFixed(2),'All accounts','Model provider charges in the last 7 days.'));
  }catch(e){fail('Could not read the credits page ('+e.message+'). Press Refresh to try again.')}
}
const grants=makeTable($('#grant-view'),'Recent Grants','Credits given to accounts, newest first.',[
  {key:'when',label:'When (UTC)',tip:'When the credits were added.'},
  {key:'account',label:'Account',tip:'Who received the credits.'},
  {key:'credits',label:'Credits',tip:'How many credits were added.',num:true,fmt:v=>num(v)},
  {key:'reason',label:'Reason',tip:'Why the credits were given.'},
  {key:'by',label:'Given By',tip:'Operator means the operator page. Script means the onboarding script.'}],
  'No grants yet.');
const gEmail=el('input',{id:'g-email',type:'email',placeholder:'name@example.com',title:'The sign-in email of the account that gets the credits.'});
const gCredits=el('input',{id:'g-credits',type:'number',min:'0.01',max:'500',step:'any',placeholder:'50',title:'How many credits to give. At most 500 at a time.'});
const gReason=el('input',{id:'g-reason',type:'text',value:'Free launch credits',maxLength:400,title:'Why the credits are given. It is kept in the credit history.'});
const gConfirm=el('input',{id:'g-confirm',type:'text',placeholder:'yes',autocomplete:'off',title:'Type "yes" to allow the grant after you have read the preview.'});
const gPreviewBtn=el('button',{id:'g-preview',textContent:'Preview',title:'Shows what the grant would do. Nothing is changed.'});
const gGrantBtn=el('button',{id:'g-grant',textContent:'Grant Credits',disabled:true,title:'Press Preview first.'});
const gOut=el('div',{id:'grant-preview',className:'empty'});
const fld=(t,i)=>el('label',{textContent:t},i);
const gForm=el('div',{id:'grant-form'},fld('Account Email',gEmail),fld('Credits',gCredits),fld('Reason',gReason),gPreviewBtn,fld('Type "yes" To Confirm',gConfirm),gGrantBtn);
$('#grant-view').prepend(gOut);$('#grant-view').prepend(gForm);
let gPreview=null,gWorking=false;
function gSync(){
  const ok=gPreview&&gPreview.found&&gPreview.scale_ok&&!gPreview.already_granted_today&&gPreview.key===gEmail.value.trim().toLowerCase()+'|'+gCredits.value;
  const yes=gConfirm.value.trim()==='yes';
  gGrantBtn.disabled=gWorking||gGrantBtn.getAttribute('aria-busy')==='true'||!(ok&&yes);
  gGrantBtn.title=!gPreview||gPreview.key!==gEmail.value.trim().toLowerCase()+'|'+gCredits.value?'Press Preview first. Changing the email or credits needs a new preview.':
    !gPreview.found?'No account has that email.':!gPreview.scale_ok?'This account uses a different credit scale.':gPreview.already_granted_today?'This exact grant was already given today.':
    !yes?'Type "yes" in the confirm box.':'Gives the credits now.';
}
[gEmail,gCredits,gConfirm].forEach(i=>i.addEventListener('input',gSync));
async function gCall(path,body){
  const r=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json().catch(()=>({}));
  if(!r.ok){const x=d.detail;throw new Error(typeof x==='string'?x:x&&x.error?x.error+' '+(x.next||''):'Error '+r.status)}
  return d;
}
gPreviewBtn.onclick=async()=>runAction('ops.grant',[gPreviewBtn,gGrantBtn],async()=>{
  gWorking=true;
  gPreview=null;gSync();gOut.textContent='Looking up the account...';
  try{
    const email=gEmail.value.trim(),credits=Number(gCredits.value);
    if(!email||!(credits>0))throw new Error('Type an email and a number of credits above 0.');
    const p=await gCall('/api/admin/operations/grant-preview',{email,credits,reason:gReason.value||'x'});
    p.key=email.toLowerCase()+'|'+gCredits.value;gPreview=p;
    gOut.textContent=!p.found?'No account has that email. They need to sign up first.':
      'Balance now: '+num(p.balance_credits)+' credits\nGrant: +'+num(credits)+' credits\nBalance after: '+num(p.after_credits)+' credits\nLimits row: '+p.admission_row+
      (p.already_granted_today?'\nThis exact grant was already given today. Nothing more can be granted.':'\nType "yes", then press Grant Credits.');
  }catch(e){gOut.textContent=e.message}
  gWorking=false;
}).finally(gSync);
gGrantBtn.onclick=async()=>runAction('ops.grant',[gPreviewBtn,gGrantBtn],async()=>{
  gWorking=true;
  try{
    const d=await gCall('/api/admin/operations/grant',{email:gEmail.value.trim(),credits:Number(gCredits.value),reason:gReason.value,confirm:gConfirm.value.trim()});
    gOut.textContent=d.granted?'Done. Balance went from '+num(d.before_credits)+' to '+num(d.after_credits)+' credits. Limits row: '+d.admission_row+'.':'Nothing was granted: this exact grant was already given today.';
    gPreview=null;gConfirm.value='';loadGrants();
  }catch(e){gOut.textContent='Not granted: '+e.message}
  gWorking=false;
}).finally(gSync);
async function loadGrants(){
  try{
    const r=await fetch('/api/admin/operations/grants',{credentials:'same-origin'});
    if(r.status===401||r.status===403){gOut.textContent='This page is for administrators.';grants.set([]);return}
    if(r.status===503){const d=((await r.json().catch(()=>({}))).detail)||{};gOut.textContent=(d.error||'Not available.')+' '+(d.next||'');grants.set([]);return}
    if(!r.ok)throw new Error(String(r.status));
    grants.set((await r.json()).grants);
  }catch(e){gOut.textContent='Could not read the grants ('+e.message+'). Press Refresh to try again.';grants.set([])}
}
gSync();
const VIEWS=['health','stops','spend','grant'];
const TITLES={health:'Server Health',stops:'Admin Stops',spend:'Credits And Spend',grant:'Grant Credits'};
function view(name){document.body.dataset.view=name;$('#page-title').textContent=TITLES[name];document.title='Requi.re '+TITLES[name];if(name==='stops')loadStops();else if(name==='spend')loadSpend();else if(name==='grant')loadGrants();else load()}
const hashView=()=>{const n=location.hash.slice(1);return VIEWS.includes(n)?n:'health'};
const onHash=()=>view(hashView());window.addEventListener('hashchange',onHash);onPageExit(()=>window.removeEventListener('hashchange',onHash));

function card(k,v,s,tip){return el('div',{className:'card',title:tip},el('div',{className:'k',textContent:k}),el('div',{className:'v',textContent:v}),el('div',{className:'s',textContent:s||''}))}

function show(h){
  if(dead)return;
  if(!$('#flags')||!$('#cards'))return;   // defense in depth: the router's epoch guard is what keeps this page alive
  $('#asof').textContent='As of '+localTime(h.as_of);
  $('#flags').replaceChildren(...h.flags.map(f=>el('div',{className:'flag '+f.level,textContent:f.text})));
  const m=h.memory,c=h.cpu,cap=h.capacity;
  $('#cards').replaceChildren(
    card('Uptime',dur(h.uptime_seconds),'Since '+h.started,'How long this server process has been running.'),
    card('Signed-In Users',String(h.signed_in_users),`Seen in the last ${h.signed_in_window_minutes} minutes`,'People who made a request recently. Counts reset when the server restarts.'),
    card('Runs',`${cap.running} of ${cap.slots}`,cap.waiting?`${cap.waiting} waiting`:'None waiting','Phases using a slot, out of the slots this server has.'),
    card('Memory',m.used_percent==null?'Not Available':m.used_percent+'%',m.used_mb==null?'':`${m.used_mb} of ${m.total_mb} MB (${m.limited_by} limit)`,'Memory in use on this server, against its limit.'),
    card('CPU Now',c.container_percent==null?'Not Available':num(c.container_percent,'%'),`of one core, ${c.cpus||'?'} cores`,'Latest CPU reading, as a percent of one core.'),
    card('CPU Last 24 Hours',c.average_24h_percent==null?'Not Available':num(c.average_24h_percent,'%'),c.peak_24h_percent==null?'':'Peak '+num(c.peak_24h_percent,'%'),'Average and peak CPU over the last 24 hours of samples.'),
    card('Time Limit',cap.time_limit_minutes+' min',cap.mode,'How long one phase may run before it is stopped.'));
  sessions.set(h.sessions);running.set(h.running);recent.set(h.recent_runs);
  $('#note').textContent=h.restart_note;
  reveal($('#cards'));reveal($('#panels'));
}

async function load(){
  try{
    const r=await fetch('/api/admin/operations/health',{credentials:'same-origin'});
    if(r.status===401||r.status===403){fail('This page is for administrators. Sign in with an administrator account, then reload.');return}
    if(!r.ok){fail('The server could not read its own numbers ('+r.status+'). Try Refresh in a moment.');return}
    show(await r.json());
  }catch(e){fail('Could not reach the server. Check your connection and press Refresh.')}
}
function fail(msg){
  if(dead)return;const f=$('#flags'),c=$('#cards');if(!f||!c)return;   // defense in depth, not the fix: the router's epoch guard owns liveness
  f.replaceChildren(el('div',{className:'flag warning',textContent:msg}));c.replaceChildren();sessions.set([]);running.set([]);recent.set([])}

$('#refresh').onclick=()=>runAction('ops.refresh',$('#refresh'),()=>{const v=document.body.dataset.view;return v==='stops'?loadStops():v==='spend'?loadSpend():v==='grant'?loadGrants():load()});
$('#dl').onclick=async()=>runAction('ops.logs',$('#dl'),async()=>{
  try{
    const r=await fetch('/api/admin/operations/log-download?minutes='+$('#mins').value,{credentials:'same-origin'});
    if(!r.ok)throw new Error(String(r.status));
    const name=(/filename="([^"]+)"/.exec(r.headers.get('Content-Disposition')||'')||[])[1]||'requi-server-log.zip';
    const url=URL.createObjectURL(await r.blob());const a=el('a',{href:url,download:name});document.body.append(a);a.click();a.remove();URL.revokeObjectURL(url);
  }catch(e){$('#flags').prepend(el('div',{className:'flag warning',textContent:'The log could not be downloaded ('+e.message+'). Only administrators can download it.'}))}
});
view(initialView||hashView());const healthTimer=setInterval(()=>{if(!dead&&document.body.dataset.view==='health')load()},10000);onPageExit(()=>clearInterval(healthTimer));
}
