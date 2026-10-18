import {api} from './client-core.js';

/* One session total for every authenticated page, independent of its content. */
export function startSessionCostHeader(){
 const host=document.querySelector('#sessioncost');if(!host)return ()=>{};
 const header=host.closest('header');
 const sizing=new ResizeObserver(()=>document.documentElement.style.setProperty('--global-header-height',`${header.getBoundingClientRect().height}px`));sizing.observe(header);
 const task=location.pathname.match(/^\/tasks\/([^/]+)(?:\/|$)/),session=location.pathname.match(/^\/sessions\/([^/]+)(?:\/|$)/);
 const route=task?`/api/tasks/${task[1]}/cost`:session?`/api/sessions/${session[1]}/cost`:null;
 if(!route){host.textContent='No Session Selected';host.dataset.state='empty';return ()=>sizing.disconnect()}
 let stopped=false,pending=false,hasValue=false;
 host.textContent='Session Cost: Loading';
 const refresh=async()=>{if(pending||stopped)return;pending=true;try{
  const value=await api(route);if(stopped)return;
  if(!Number.isFinite(value.price)||!Number.isFinite(value.price_usd))throw new Error('Cost unavailable');
  host.textContent=`${value.price.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})} credits · $${value.price_usd.toFixed(2)}`;
  host.dataset.state='current';hasValue=true;host.title='Rolling Session Cost';
 }catch(error){if(stopped)return;
  if(!hasValue&&error&&error.status===404){host.textContent='No Session Selected';host.dataset.state='empty';host.title='';return}
  host.dataset.state=hasValue?'stale':'unavailable';host.title='Cost Could Not Be Refreshed';
  if(hasValue){if(!host.textContent.endsWith(' · Last Known'))host.textContent+=' · Last Known'}else host.textContent='Session Cost Unavailable';
 }finally{pending=false}};
 refresh();const timer=setInterval(()=>{if(!document.hidden)refresh()},30000);const visible=()=>{if(!document.hidden)refresh()};document.addEventListener('visibilitychange',visible);
 const dispose=()=>{stopped=true;clearInterval(timer);document.removeEventListener('visibilitychange',visible);sizing.disconnect()};window.addEventListener('pagehide',dispose,{once:true});return dispose;
}
