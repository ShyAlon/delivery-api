import {esc} from './client-core.js';
import {onPageExit} from './page-lifecycle.js';
import {humanLabel,humanizeSnakeCase} from './format.js';
export const isProgressEvent = e => ['progress','heartbeat'].includes(e?.kind);
export const progressLine = e => {const message=humanizeSnakeCase(e.message);return `<div class="progress-item ${esc(e.kind)}"><span title="${esc(message)}">${esc(message)}</span></div>`};
export function elapsedLabel(startedAt){if(!startedAt)return '';let seconds=Math.max(0,Math.floor(Date.now()/1000-startedAt));if(seconds<60)return '<b>&lt;1</b> minute so far';let minutes=Math.floor(seconds/60);if(minutes<60)return `<b>${minutes}</b> ${minutes===1?'minute':'minutes'} so far`;let hours=Math.floor(minutes/60),remainder=minutes%60;return `<b>${hours}</b> ${hours===1?'hour':'hours'}${remainder?` <b>${remainder}</b> minutes`:''} so far`}
export const ANSWER_PENDING_LABEL='Processing your answer…';
export const WAITING_MODES={engine_question:['Waiting for your answer','The engine asked a question. Answer it below to continue.'],engine_decision:['Waiting for your decision','The engine needs a choice from you. Choose below to continue.'],phase_gate:['Waiting for your approval','This phase is ready. Review it and approve to continue.']};
export function nextPhasePanel(label){return `<section class="next-phase-panel" id="nextphase" role="status"><div><b>Interview complete</b><span>${esc(label)} is next. It uses your credits as it runs, and nothing has been charged for it yet.</span></div><button class="primary next-phase-button" id="startnext" type="button" title="Starts ${esc(label)} and uses your credits. You can stop it at any time.">Start ${esc(label)}</button></section>`}
export function waitingBanner(mode){const w=WAITING_MODES[mode];return w?`<div class="waiting-banner" id="waitingbanner" role="status"><b>${esc(w[0])}</b><span>${esc(w[1])}</span></div>`:''}
export function progressScope(phase,runStartedAt=null,waiting=false){let startedAt=Math.max(Number(phase?.started_at||0),Number(runStartedAt||0));if(!startedAt)return '';let count=phase.total?`<span>${esc(humanLabel(phase.phase_key))} <b>${Number(phase.completed||0)}</b> out of <b>${Number(phase.total)}</b></span>`:'';return `<div class="progress-scope" data-started-at="${startedAt}"${waiting?' data-waiting="1"':''}>${count}${count?'<i>·</i>':''}<span>${waiting?'Waiting for you':elapsedLabel(startedAt)}</span></div>`}
export function updateElapsedScopes(){document.querySelectorAll('.progress-scope[data-started-at]:not([data-waiting])').forEach(scope=>{let spans=scope.querySelectorAll('span'),target=spans[spans.length-1];if(target)target.innerHTML=elapsedLabel(Number(scope.dataset.startedAt))})}
export const stageEvent=(events,afterId=0)=>[...(events||[])].reverse().find(e=>e.id>=afterId&&(e.payload?.label||e.kind==='progress'));
export const stageName=e=>e?humanizeSnakeCase(String(e.payload?.label||e.message||'')):'';

// UI-only state keyed by task/review scope. Stream and snapshot renders must not
// undo an explicit expansion. Nothing here writes to server state.
const expandedProgress=new Map();
document.addEventListener('toggle',event=>{const box=event.target;if(box.isConnected&&box.matches?.('details[data-progress-key]'))expandedProgress.set(box.dataset.progressKey,box.open)},true);
// The in-flight label names the newest engine signal observed AFTER this answer
// was submitted; without one it stays the honest blanket label. Never a guess.
// The anchor is the tab-local shadow when present, else the durable
// answer_submitted event written at submit - a re-render in a fresh tab loses
// the shadow but must not stay blanket for the whole turn.
// Seconds when the answer now being processed was sent: this tab's own stamp, else the durable
// answer_submitted event of an unfinished turn. null when neither exists: never the run start.
export function answerStartedAt(events,{afterId=0,sinceMs=0}={}){
 if(sinceMs)return sinceMs/1000;
 const list=events||[],at=list.findLastIndex(e=>e.kind==='answer_submitted'&&Number(e.id||0)>=afterId);
 if(at<0||list.slice(at+1).some(e=>['waiting_input','answer','gate','complete','error'].includes(e.kind)))return null;
 return signalTime(list[at]);
}
// Which progress belongs to the answer being processed is decided by event id, a server-ordered sequence that
// needs no clock agreement between this tab and the server. The id floor is the newest event this tab had when it
// submitted (sinceId), else the durable answer_submitted event of an unfinished turn. Only when neither exists
// (an older stored shadow) does it fall back to the tab clock against server stamps.
export function answerProcessingLabel(events,{phaseKey,afterId=0,sinceMs=0,sinceId=0}={}){
 const list=events||[];
 let floorId=Number(sinceId||0),floorMs=0;
 if(!floorId){
  const at=list.findLastIndex(e=>e.kind==='answer_submitted'&&Number(e.id||0)>=afterId);
  const live=at>=0&&!list.slice(at+1).some(e=>['waiting_input','answer','gate','complete','error'].includes(e.kind));
  if(live&&Number(list[at].id||0)>0)floorId=Number(list[at].id);
  else if(sinceMs)floorMs=sinceMs-1500;
  else if(live)floorMs=(signalTime(list[at])??0)*1000;
 }
 if(!floorId&&!floorMs)return ANSWER_PENDING_LABEL;
 const latest=[...list].reverse().find(e=>e.kind==='progress'&&Number(e.id||0)>=afterId&&(!e.phase_key||e.phase_key===phaseKey)&&(floorId?Number(e.id||0)>floorId:(signalTime(e)??0)*1000>floorMs));
 return latest?`Processing your answer · ${humanizeSnakeCase(String(latest.message||latest.payload?.label||''))}`:ANSWER_PENDING_LABEL;
}
export function activePhaseProgress(events,{phaseKey,afterId=0,mode='executing',fallback='Working',unavailable='',answerPending=false,answerSince=0,answerSinceId=0}={}){
 if(unavailable)return unavailable;
 if(WAITING_MODES[mode])return answerPending?answerProcessingLabel(events,{phaseKey,afterId,sinceMs:answerSince,sinceId:answerSinceId}):WAITING_MODES[mode][0];
 if(['error','stopped','stop_requested'].includes(mode))return fallback;
 const latest=[...(events||[])].reverse().find(e=>Number(e.id||0)>=afterId&&isProgressEvent(e)&&(!e.phase_key||e.phase_key===phaseKey));
 return latest?humanizeSnakeCase(String(latest.message||latest.payload?.label||fallback)):fallback;
}
export function renderStageProgress(host,events,options){
 if(!host)return;
 const box=host.querySelector('details[data-progress-key]');
 if(box)expandedProgress.set(box.dataset.progressKey,box.open);
 host.innerHTML=stageProgressHtml(events,options);
}
// Same renderer for task and blocking review-apply progress. Counts are source
// values only; no percent or guessed hard-coded completion time.
// Unknown event time is not epoch zero or the run start. Keep signal age
// absent until a source timestamp supports it; stage timing may use run start.
function signalTime(event){
 const values=[Date.parse(event.payload?.timestamp)/1000,Number(event.ts)];
 return values.find(value=>Number.isFinite(value)&&value>0)??null;
}
// Plain words for the one shared progress summary. connected is tri-state: null
// means the stream has not answered yet (never reported as lost). While the user
// owes the next step nothing is running, so no elapsed time is claimed.
export function progressStatusText({connected,waiting,elapsed,signalAgo}){
 if(waiting)return 'Nothing is running. The engine is waiting for you.';
 const link=connected===null?'Connecting to live updates':connected?'Live':'Live updates paused, retrying. Your work is safe';
 return `${link} · ${elapsed}s on this step${signalAgo!==null?` · last update ${signalAgo}s ago`:''}`;
}
export function stageProgressHtml(events,{startedAt=Date.now()/1000,connected=true,nextStep='choose the operating concept when the engine requests it',unavailable='',scopeKey='review',currentLine='',signalAge=true,waiting=false}={}){
 // A session that is unavailable in this environment has no live counters, no estimate and no next step to promise: only what was last recorded.
 if(unavailable)return `<details class="review-progress" data-progress-key="${esc(scopeKey)}" ${expandedProgress.get(scopeKey)?'open':''} aria-label="Run progress"><summary><b>Run progress</b><span>${esc(unavailable)}</span></summary><div class="progress-history"><p>No live counters or next step can be confirmed in this environment.</p></div></details>`;
 const stages=new Map(),measurements=new Map();let lastSignal=null;
 for(const e of events||[]){const p=e.payload||{};if(!p.stage||!p.status)continue;const key=p.stage+'|'+(p.label||'');
  const observed=signalTime(e),ts=observed??startedAt;if(observed!==null)lastSignal=Math.max(lastSignal??0,observed);
  const old=stages.get(key)||{label:p.label||p.stage,start:ts};
  if(p.status==='item_completed'&&old.status==='item_started'&&ts>old.start&&p.completed===Number(old.completed||0)+1){
   const samples=measurements.get(key)||[];samples.push(ts-old.start);measurements.set(key,samples);
  }
  if(p.status==='item_started')old.start=ts;
  stages.set(key,{...old,stage:p.stage,label:p.label||old.label,status:p.status,completed:p.completed??old.completed,total:p.total??old.total,detail:p.detail||old.detail,end:p.status==='completed'||p.status==='item_completed'&&p.completed===p.total?ts:old.end});
 }
 const latest=(events||[]).findLast(isProgressEvent);
 if(latest){const observed=signalTime(latest);if(observed!==null)lastSignal=Math.max(lastSignal??0,observed);}
 const rows=[...stages.values()],now=Date.now()/1000,active=rows.findLast(s=>!['completed','failed'].includes(s.status)&&!(s.status==='item_completed'&&s.completed===s.total));
 // Only item-level paired observations support an ETA. Field calls may
 // complete multiple times for one item; dividing by total would undercount.
 const samples=(active?(measurements.get(active.stage+'|'+active.label)||[]):[]).slice().sort((a,b)=>a-b);
 const remaining=active&&Number.isFinite(active.total)&&active.total>Number(active.completed||0)?active.total-Number(active.completed||0):null;
 let eta='Estimating';
 if(samples.length&&remaining!==null){const low=samples[0]*remaining,high=Math.max(samples.at(-1),now-active.start)*remaining;eta=`Measured estimate: ${Math.max(1,Math.floor(low/60))}-${Math.max(1,Math.ceil(high/60))} minutes for the current counted work`;}
 return `<details class="review-progress" data-progress-key="${esc(scopeKey)}" ${expandedProgress.get(scopeKey)?'open':''} aria-label="Run progress"><summary><b>Run progress</b><span>${esc(progressStatusText({connected,waiting,elapsed:Math.max(0,Math.floor(now-startedAt)),signalAgo:lastSignal!==null&&signalAge?Math.max(0,Math.floor(now-lastSignal)):null}))}</span>${currentLine||latest?`<span class="progress-current">${esc(currentLine||humanizeSnakeCase(latest.message))}</span>`:''}</summary><div class="progress-history"><p>${esc(eta)}</p>${latest?progressLine(latest):''}${rows.length?`<ol>${rows.map(s=>`<li class="${esc(s.status)}"><details ${s===active?'open':''}><summary>${s.status==='completed'||s.status==='item_completed'&&s.completed===s.total?'Completed':s.status==='failed'?'Failed':'Working'} · ${esc(humanLabel(s.label))}${Number.isFinite(s.total)?` · ${Number(s.completed||0)}/${Number(s.total)}`:''}</summary>${s.detail?`<p>${esc(s.detail)}</p>`:''}</details></li>`).join('')}</ol>`:'<p>Waiting for stage progress.</p>'}<p>Next: ${esc(nextStep)}.</p></div></details>`;
}

// While the progress stream is open the page polls only once a minute: stream events
// trigger an immediate poll, the timer is the safety net (2026-10-27 ruling - polled
// database reads are not a heartbeat). A dead stream returns polling to 1.5s.
export const REVIEW_POLL_STREAM_MS=60000;
export const shouldPollNow=(streamOpen,lastPollMs,nowMs)=>!streamOpen||nowMs-lastPollMs>=REVIEW_POLL_STREAM_MS;

export function watchReviewProgress(taskId,host){
 const events=[],startedAt=Date.now()/1000;let connected=null,after=0,busy=false,closed=false,lastPoll=0,streamOpen=false;
 const render=()=>{if(host?.isConnected)renderStageProgress(host,events,{startedAt,connected,scopeKey:'review:'+taskId,currentLine:'Saving your decisions. The engine checks them before the next step; nothing is resent.'})};
 const poll=async(force)=>{if(closed||busy||!host?.isConnected)return;if(!force&&!shouldPollNow(streamOpen,lastPoll,Date.now()))return;busy=true;lastPoll=Date.now();try{
  const response=await fetch(`/api/tasks/${encodeURIComponent(taskId)}/events?after=${after}`,{cache:'no-store'});
  if(!response.ok)throw new Error('progress unavailable');
  const page=await response.json(),rows=Array.isArray(page)?page:page.events||[];
  for(const e of rows){if(e.id>after){events.push(e);after=e.id;}}
  connected=true;
 }catch{connected=false}finally{busy=false;render()}};
 const es=new EventSource(`/api/tasks/${encodeURIComponent(taskId)}/stream`);
 es.onopen=()=>{streamOpen=true;connected=true;render()};
 es.onerror=()=>{streamOpen=false;connected=false;render()};
 es.addEventListener('progress',()=>{poll(true)});
 render();poll(true);const timer=setInterval(()=>poll(false),1500);onPageExit(()=>{clearInterval(timer);es.close()});
 return ()=>{closed=true;clearInterval(timer);es.close()};
}
