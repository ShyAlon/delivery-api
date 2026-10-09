import {esc} from './client-core.js';
import {humanLabel,humanizeSnakeCase} from './format.js';
export const isProgressEvent = e => ['progress','heartbeat'].includes(e?.kind);
export const progressLine = e => {const message=humanizeSnakeCase(e.message);return `<div class="progress-item ${esc(e.kind)}"><span title="${esc(message)}">${esc(message)}</span></div>`};
export function elapsedLabel(startedAt){if(!startedAt)return '';let seconds=Math.max(0,Math.floor(Date.now()/1000-startedAt));if(seconds<60)return '<b>&lt;1</b> minute so far';let minutes=Math.floor(seconds/60);if(minutes<60)return `<b>${minutes}</b> ${minutes===1?'minute':'minutes'} so far`;let hours=Math.floor(minutes/60),remainder=minutes%60;return `<b>${hours}</b> ${hours===1?'hour':'hours'}${remainder?` <b>${remainder}</b> minutes`:''} so far`}
export function progressScope(phase,runStartedAt=null){let startedAt=Math.max(Number(phase?.started_at||0),Number(runStartedAt||0));if(!startedAt)return '';let count=phase.total?`<span>${esc(humanLabel(phase.phase_key))} <b>${Number(phase.completed||0)}</b> out of <b>${Number(phase.total)}</b></span>`:'';return `<div class="progress-scope" data-started-at="${startedAt}">${count}${count?'<i>·</i>':''}<span>${elapsedLabel(phase.started_at)}</span></div>`}
export function updateElapsedScopes(){document.querySelectorAll('.progress-scope[data-started-at]').forEach(scope=>{let spans=scope.querySelectorAll('span'),target=spans[spans.length-1];if(target)target.innerHTML=elapsedLabel(Number(scope.dataset.startedAt))})}
export const stageEvent=(events,afterId=0)=>[...(events||[])].reverse().find(e=>e.id>=afterId&&(e.payload?.label||e.kind==='progress'));
export const stageName=e=>e?humanizeSnakeCase(String(e.payload?.label||e.message||'')):'';

// Same renderer for task and blocking review-apply progress. Counts are source
// values only; no percent or guessed hard-coded completion time.
export function stageProgressHtml(events,{startedAt=Date.now()/1000,connected=true,nextStep='choose the operating concept when the engine requests it'}={}){
 const stages=new Map(),measurements=new Map();let lastSignal=null;
 for(const e of events||[]){const p=e.payload||{};if(!p.stage||!p.status)continue;const key=p.stage+'|'+(p.label||'');
  const ts=Date.parse(p.timestamp)/1000||Number(e.ts)||startedAt;lastSignal=Math.max(lastSignal||0,ts);
  const old=stages.get(key)||{label:p.label||p.stage,start:ts};
  if(p.status==='item_completed'&&old.status==='item_started'&&ts>old.start&&p.completed===Number(old.completed||0)+1){
   const samples=measurements.get(key)||[];samples.push(ts-old.start);measurements.set(key,samples);
  }
  if(p.status==='item_started')old.start=ts;
  stages.set(key,{...old,stage:p.stage,label:p.label||old.label,status:p.status,completed:p.completed??old.completed,total:p.total??old.total,detail:p.detail||old.detail,end:p.status==='completed'||p.status==='item_completed'&&p.completed===p.total?ts:old.end});
 }
 const latest=(events||[]).findLast(isProgressEvent);
 const rows=[...stages.values()],now=Date.now()/1000,active=rows.findLast(s=>!['completed','failed'].includes(s.status)&&!(s.status==='item_completed'&&s.completed===s.total));
 // Only item-level paired observations support an ETA. Field calls may
 // complete multiple times for one item; dividing by total would undercount.
 const samples=(active?(measurements.get(active.stage+'|'+active.label)||[]):[]).slice().sort((a,b)=>a-b);
 const remaining=active&&Number.isFinite(active.total)&&active.total>Number(active.completed||0)?active.total-Number(active.completed||0):null;
 let eta='Estimating';
 if(samples.length&&remaining!==null){const low=samples[0]*remaining,high=Math.max(samples.at(-1),now-active.start)*remaining;eta=`Measured estimate: ${Math.max(1,Math.floor(low/60))}-${Math.max(1,Math.ceil(high/60))} minutes for the current counted work`;}
 return `<section class="review-progress" aria-label="Run progress"><h3>Run progress</h3><p>${connected?'Connected to progress':'Progress connection lost; reconnecting without resubmitting'} · ${Math.max(0,Math.floor(now-startedAt))}s elapsed${lastSignal!==null?` · last engine signal ${Math.max(0,Math.floor(now-lastSignal))}s ago`:''}</p><p>${esc(eta)}</p>${latest?progressLine(latest):''}${rows.length?`<ol>${rows.map(s=>`<li class="${esc(s.status)}"><details ${s===active?'open':''}><summary>${s.status==='completed'||s.status==='item_completed'&&s.completed===s.total?'Completed':s.status==='failed'?'Failed':'Working'} · ${esc(humanLabel(s.label))}${Number.isFinite(s.total)?` · ${Number(s.completed||0)}/${Number(s.total)}`:''}</summary>${s.detail?`<p>${esc(s.detail)}</p>`:''}</details></li>`).join('')}</ol>`:'<p>Waiting for stage progress.</p>'}<p>Next: ${esc(nextStep)}.</p></section>`;
}

export function watchReviewProgress(taskId,host){
 const events=[],startedAt=Date.now()/1000;let connected=true,after=0,busy=false,closed=false;
 const render=()=>{if(host?.isConnected)host.innerHTML='<p>Recording decisions. Reading engine progress without resubmitting.</p>'+stageProgressHtml(events,{startedAt,connected})};
 const poll=async()=>{if(closed||busy||!host?.isConnected)return;busy=true;try{
  const response=await fetch(`/api/tasks/${encodeURIComponent(taskId)}/events?after=${after}`,{cache:'no-store'});
  if(!response.ok)throw new Error('progress unavailable');
  const page=await response.json(),rows=Array.isArray(page)?page:page.events||[];
  for(const e of rows){if(e.id>after){events.push(e);after=e.id;}}
  connected=true;
 }catch{connected=false}finally{busy=false;render()}};
 render();poll();const timer=setInterval(poll,1500);
 return ()=>{closed=true;clearInterval(timer)};
}
