import {scrubText,buildClientReport} from './log-scrub.js';
export const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export const time = timestamp => timestamp ? new Date(timestamp*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : '—';
export const fullTime = timestamp => timestamp ? new Intl.DateTimeFormat([], {year:'numeric',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',timeZoneName:'short'}).format(new Date(timestamp*1000)) : '—';

export function clientLog(kind,message,detail={}){
  const entry={ts:new Date().toISOString(),kind,message,path:location.pathname,...detail};
  (kind.includes('error')||kind.includes('failure')?console.error:console.info)(`[Requi.re ${entry.ts}] ${kind}: ${message}`,detail);
  fetch('/api/client-log',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({kind,message,path:location.pathname,detail}),keepalive:true}).catch(()=>{});
}

const browserCapture={started_at:new Date().toISOString(),records:[]};
function capture(kind,message){browserCapture.records.push({ts:new Date().toISOString(),kind,path:location.pathname,message:String(message).slice(0,2000)});if(browserCapture.records.length>300)browserCapture.records.shift()}
for(const kind of ['log','info','warn','error']){const original=console[kind].bind(console);console[kind]=(...args)=>{try{capture(kind,args.map(x=>typeof x==='string'?x:JSON.stringify(x)).join(' '))}catch{}original(...args)}}
window.addEventListener('error',e=>capture('error',e.message));window.addEventListener('unhandledrejection',e=>capture('unhandledrejection',e.reason?.message||e.reason));
const requestLog=[];
export function recentRequests(){return requestLog.slice()}
/** Plain-text table for the Debug dialog: total time in the browser, server time, status, reference, request. */
export function requestTable(){
  const rows=requestLog.slice(-40).reverse().map(r=>`${String(r.total_ms).padStart(6)} ms  server ${r.server_ms===null?'   ?':String(r.server_ms).padStart(4)} ms  ${r.status}  ref ${r.ref||'-'}  ${r.method} ${r.url}${r.db?'  [database '+r.db+']':''}${r.total_ms>=2000?'  SLOW':''}`);
  return rows.length?'browser total | server time | status | reference | request\n'+rows.join('\n'):'No requests yet.';
}
export function clientReport(build){return buildClientReport({requests:requestLog.slice(),records:browserCapture.records.slice(),startedAt:browserCapture.started_at,url:location.href,build})}
export async function serverReport(minutes=30){const r=await api(`/api/server-log?minutes=${minutes}`);return {text:scrubText(r.text||'')+'\n',range:`${r.scope}, last ${r.minutes} minutes (${r.since} to ${r.until}), ${r.count} lines${r.buffer_full?', oldest lines already dropped (buffer full)':''}`}}
export async function deliverText(text,filename){try{await navigator.clipboard.writeText(text);return 'copied'}catch{const url=URL.createObjectURL(new Blob([text],{type:'text/plain'})),a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);return 'downloaded'}}
export function browserDiagnostics(){return {...browserCapture,ended_at:new Date().toISOString(),url:location.href,records:browserCapture.records.slice()}}
export async function downloadDebug(taskId){
 await renewSessionIfDue();
 const response=await fetch(`/api/tasks/${encodeURIComponent(taskId)}/debug.zip`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(browserDiagnostics())});
 if(!response.ok){let data=await response.json();throw new Error(data.detail?.error||data.detail||'Debug export failed')}
 const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`${taskId}-debug.zip`;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
}

let refreshExpiresAt=0, refreshInFlight=null, lastActivity=Date.now();
if(typeof document!=='undefined'){for(const name of ['pointerdown','keydown'])document.addEventListener(name,()=>{lastActivity=Date.now()},{passive:true})}
export function setSessionExpiry(expiresAt){refreshExpiresAt=Number(expiresAt)||0}
export function noteActivity(){lastActivity=Date.now()}
export async function renewSessionIfDue(){
  if(!refreshExpiresAt || Date.now()/1000 < refreshExpiresAt-600 || Date.now()-lastActivity>20*60*1000)return;
  if(!refreshInFlight)refreshInFlight=fetch('/auth/refresh',{method:'POST',headers:{'content-type':'application/json'}})
    .then(async response=>{if(!response.ok){refreshExpiresAt=0;throw new Error('Your sign-in expired. Sign in again before continuing.')}refreshExpiresAt=Number((await response.json()).expires_at)||0})
    .finally(()=>{refreshInFlight=null});
  return refreshInFlight;
}

let pendingWaits=0, waitTimer=null;
function waitLabel(url){
  if(/\/start$/.test(url))return 'Starting interview';
  if(/\/answer$/.test(url))return 'Submitting answer';
  if(/\/model-route$/.test(url))return 'Switching interview model';
  if(/\/gates\//.test(url))return 'Saving gate decision';
  if(/\/feedback/.test(url))return 'Saving feedback';
  if(/\/requirements\//.test(url))return 'Checking required input';
  if(/\/audit$/.test(url))return 'Reading audit';
  if(/\/rerun/.test(url))return 'Preparing rerun';
  if(/\/retry-needs/.test(url))return 'Retrying stakeholder needs';
  if(/\/artifacts\//.test(url))return 'Reading artifact';
  if(/\/api\/sessions/.test(url))return 'Loading session';
  if(/\/phase-output$/.test(url))return 'Reading phase results';
  if(/\/tasks\/[^/]+$/.test(url))return 'Loading task';
  return 'Waiting for the server';
}
function beginWait(url){
  const banner=typeof document==='undefined'?null:document.querySelector('#server-wait');
  if(!banner)return ()=>{};
  pendingWaits++;
  if(!waitTimer)waitTimer=setTimeout(()=>{
    banner.querySelector('span').textContent=waitLabel(url);
    banner.classList.toggle('answering',/\/answer$/.test(url));
    banner.classList.remove('hidden');
  },350);
  let finished=false;
  return ()=>{
    if(finished)return;
    finished=true;pendingWaits=Math.max(0,pendingWaits-1);
    if(!pendingWaits){clearTimeout(waitTimer);waitTimer=null;banner.classList.add('hidden')}
  };
}

export async function api(url,opts={}){
  if(url!=='/auth/refresh' && url!=='/api/auth' && !url.startsWith('/auth/sign') && !url.startsWith('/auth/email-availability'))await renewSessionIfDue();
  const began=performance.now();
  const method=(opts.method||'GET').toUpperCase(),timeout=opts.timeout===undefined?(method==='GET'?60000:null):opts.timeout,controller=timeout?new AbortController():null;
  const foregroundRead=method==='GET'&&(/\/api\/tasks\/[^/]+$/.test(url)||/\/api\/tasks\/[^/]+\/artifacts\//.test(url)||/\/api\/sessions/.test(url)||/\/api\/tasks\/[^/]+\/audit$/.test(url));
  const endWait=opts.inlineProgress||(method==='GET'&&!foregroundRead)||url==='/api/client-log'||url==='/auth/refresh'||url.startsWith('/auth/')?()=>{}:beginWait(url);
  let timedOut=false,timer=timeout?setTimeout(()=>{timedOut=true;controller.abort()},timeout):null,response;
  try{response=await fetch(url,{headers:{'content-type':'application/json'},...opts,signal:opts.signal||(controller&&controller.signal)})}
  catch(error){
    if(error.name==='AbortError'&&timedOut)throw new Error('Error: The request timed out.\nLikely cause: the server did not answer a read request within 60 seconds.\nNext: check the server log and retry the page load.');
    if(error.name==='AbortError')throw error;
    throw new Error(`Error: The server could not be reached.\nLikely cause: ${error.message||'The connection failed.'}\nNext: your text is preserved. Check the server is running, then retry.`);
  }finally{if(timer)clearTimeout(timer);endWait()}
  const ref=response.headers.get('x-request-id'),serverMs=Number((/dur=([0-9.]+)/.exec(response.headers.get('server-timing')||'')||[])[1]),totalMs=Math.round(performance.now()-began);
  const entry={ts:new Date().toISOString(),method,url,status:response.status,total_ms:totalMs,server_ms:Number.isFinite(serverMs)?Math.round(serverMs):null,ref,db:((/db;dur=([0-9.]+);desc="([^"]*)"/.exec(response.headers.get('server-timing')||'')||[]).slice(1,3).join(' ms, '))||null};
  requestLog.push(entry);if(requestLog.length>200)requestLog.shift();
  capture('network',`${method} ${url} -> ${response.status} ${totalMs} ms (server ${entry.server_ms===null?'unknown':entry.server_ms+' ms'}) ref ${ref||'none'}`);
  if(!response.ok){
    const raw=await response.text();let detail;
    try{detail=JSON.parse(raw).detail}catch{}
    const info=typeof detail==='object'?detail:{error:typeof detail==='string'?detail:raw};
    const message=`Error: ${info.error||'The request failed.'}\nLikely cause: ${info.cause||(response.status===404?'This item does not exist, or it is not in your account.':'The server could not complete this action.')}\nNext: ${info.next||(response.status===404?'Go back to All sessions and pick one of your own tasks.':'Retry once. If it fails again, open the task audit and server log.')}${ref?`\nReference: ${ref} (the avatar Debug dialog lists it with its timing)`:''}`;
    const error=new Error(message);error.status=response.status;error.info=info;error.endpoint=url;
    clientLog('api.error',message,{endpoint:url,http_status:response.status,ref,task_state:info.current_state,active_phase:info.active_phase,decision_id:opts.decisionId});
    throw error;
  }
  return response.json();
}
