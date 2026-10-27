import {api,noteActivity} from './client-core.js';
import {balanceLabel,balanceTitle} from './account-balance.js';
import {costTitle} from './cost-breakdown.js';

/* The customer's own prepaid credit balance, next to the session cost. Hidden when the server has no prepaid accounts. */
function startBalance(cost){
 let el=document.querySelector('#accountbalance');
 if(!el){el=document.createElement('span');el.id='accountbalance';el.className='session-cost balance';el.setAttribute('role','status');el.setAttribute('aria-label','Credit Balance');el.hidden=true;cost.insertAdjacentElement('beforebegin',el)}
 let stopped=false,pending=false;
 const refresh=async()=>{if(pending||stopped)return;pending=true;try{
  const reply=await api('/api/account/credits'),label=balanceLabel(reply);if(stopped)return;
  if(label===null){el.hidden=true;return}
  el.textContent=label;el.hidden=false;el.dataset.state=reply.credits>0?'current':'unavailable';el.title=balanceTitle(reply);
 }catch{if(stopped)return;if(!el.hidden){el.dataset.state='stale';if(!el.textContent.endsWith(' · Last Known'))el.textContent+=' · Last Known'}}finally{pending=false}};
 return {refresh,dispose:()=>{stopped=true}};
}

/* One session total for every authenticated page, independent of its content. */
export function startSessionCostHeader(){
 const host=document.querySelector('#sessioncost');if(!host)return ()=>{};
 const header=host.closest('header');const balance=startBalance(host);balance.refresh();
 const sizing=new ResizeObserver(()=>document.documentElement.style.setProperty('--global-header-height',`${header.getBoundingClientRect().height}px`));sizing.observe(header);
 const task=location.pathname.match(/^\/tasks\/([^/]+)(?:\/|$)/);
 const route=task?`/api/tasks/${task[1]}/cost`:null;
 if(!route){host.textContent='No Session Selected';host.dataset.state='empty';const visible=()=>{if(!document.hidden){noteActivity();balance.refresh()}};const timer=setInterval(visible,60000);document.addEventListener('visibilitychange',visible);window.addEventListener('requi:usage-change',visible);return ()=>{clearInterval(timer);document.removeEventListener('visibilitychange',visible);window.removeEventListener('requi:usage-change',visible);sizing.disconnect();balance.dispose()}}
 let stopped=false,pending=false,hasValue=false,eventQueued=false;
 const here=`/tasks/${task[1]}`,stillHere=()=>location.pathname===here||location.pathname.startsWith(here+'/');
 const empty=()=>{host.textContent='No Session Selected';host.dataset.state='empty';host.title='';hasValue=false};
 host.textContent='Session Cost: Loading';
 const refresh=async()=>{if(pending||stopped)return;
  if(!stillHere()){empty();return}   // the page no longer names this task or session (for example after Delete): do not ask about it
  pending=true;try{
  const value=await api(route);if(stopped)return;
  if(!stillHere()){empty();return}
  if(value.evidence?.complete===false){host.textContent='Session Cost Incomplete';host.dataset.state='unavailable';host.title=`Local usage evidence incomplete: ${value.evidence.rejected_rows} rejected rows; ${value.evidence.invalid_numeric_rows||0} invalid numeric rows. Source preserved; missing amounts unknown.`;hasValue=false;return}
  if(!Number.isFinite(value.price)||!Number.isFinite(value.price_usd))throw new Error('Cost unavailable');
  host.textContent=`${value.price.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})} credits · $${value.price_usd.toFixed(2)}`;
  host.dataset.state='current';hasValue=true;host.title='Rolling Session Cost';
  if(task){try{const parts=await api(`${route}-phases`);if(!stopped&&stillHere())host.title=costTitle(value.price,parts.phases)}catch{/* the total stands; the breakdown is optional */}}
 }catch(error){if(stopped)return;
  if(error&&error.status===404){empty();return}
  host.dataset.state=hasValue?'stale':'unavailable';host.title='Cost Could Not Be Refreshed';
  if(hasValue){if(!host.textContent.endsWith(' · Last Known'))host.textContent+=' · Last Known'}else host.textContent='Session Cost Unavailable';
 }finally{pending=false;if(eventQueued&&!stopped){eventQueued=false;queueMicrotask(refresh)}}};
 const refreshAll=()=>{balance.refresh();refresh()};
 refresh();const timer=setInterval(()=>{if(!document.hidden)refreshAll()},60000);
 const visible=()=>{if(!document.hidden){noteActivity();refreshAll()}};
 let eventTimer=null;
 const changed=()=>{clearTimeout(eventTimer);eventTimer=setTimeout(()=>{balance.refresh();if(pending)eventQueued=true;else refresh()},150)};
 document.addEventListener('visibilitychange',visible);window.addEventListener('requi:usage-change',changed);
 const dispose=()=>{stopped=true;clearInterval(timer);clearTimeout(eventTimer);balance.dispose();document.removeEventListener('visibilitychange',visible);window.removeEventListener('requi:usage-change',changed);sizing.disconnect()};window.addEventListener('pagehide',dispose,{once:true});return dispose;
}
