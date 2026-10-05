import {api,esc} from './client-core.js';
import {humanLabel} from './format.js';

const rows=(value,...keys)=>{if(Array.isArray(value))return value;if(value&&typeof value==='object')for(const key of keys)if(Array.isArray(value[key]))return value[key];return []};
const COUNTS=[
 {key:'requirements',label:'Requirements',phase:'srs',names:['srs.json'],count:v=>rows(v,'functional','requirements').length+rows(v?.non_functional).length},
 {key:'specs',label:'Specs',phase:'specs',names:['items.json','spec_items.json'],count:v=>rows(v,'items','specs').length},
 {key:'tests',label:'Tests',phase:'tests',names:['tests.json','test_specifications.json'],count:v=>rows(v,'tests').length},
 {key:'validation',label:'Validation Items',phase:'validation',names:['validation.json'],count:v=>rows(v,'items').length}
];
const credits=n=>Number(n||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
const usd=n=>'$'+Number(n||0).toFixed(2);
export function durationLabel(seconds){seconds=Math.max(0,Math.round(Number(seconds)||0));if(seconds<60)return '<1 min';const h=Math.floor(seconds/3600),m=Math.floor(seconds%3600/60);return h?`${h} h ${m} min`:`${m} min`}
export function confetti(host){
 if(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)return;
 const layer=document.createElement('div');layer.className='confetti';layer.setAttribute('aria-hidden','true');
 const colors=['#c6f26b','#1f7a4d','#f2b84b','#e8e4d8','#0f1f17'];
 for(let i=0;i<60;i++){const piece=document.createElement('i');piece.style.cssText=`left:${Math.random()*100}%;background:${colors[i%colors.length]};animation-delay:${(Math.random()*.6).toFixed(2)}s;animation-duration:${(1.8+Math.random()*1.4).toFixed(2)}s;--drift:${Math.round(Math.random()*160-80)}px`;layer.append(piece)}
 host.append(layer);setTimeout(()=>layer.remove(),4200)}
export function createCompletion(ctx){
 async function completePage(taskId){
  const app=document.querySelector('#app'),id=encodeURIComponent(taskId);
  const [t,audit]=await Promise.all([api(`/api/tasks/${id}`),api(`/api/tasks/${id}/audit`).catch(()=>null)]);
  if(t.state!=='done'){location.replace(`/tasks/${id}/view`);return}
  const events=t.events||[],priceOf=key=>events.filter(e=>e.kind==='price'&&e.phase_key===key).pop()?.payload,
   starts=t.phases.map(p=>Number(p.started_at||0)).filter(Boolean),ends=t.phases.map(p=>Number(p.completed_at||0)).filter(Boolean),
   start=starts.length?Math.min(...starts):0,end=ends.length?Math.max(...ends):0;
  const counts=await Promise.all(COUNTS.map(async c=>{
   const art=(t.artifacts||[]).find(a=>a.phase_key===c.phase&&c.names.includes(a.name)&&!a.stale&&a.present!==false);
   if(!art)return {...c,value:null};
   try{return {...c,value:c.count(await api(`/api/tasks/${id}/artifacts/${encodeURIComponent(c.phase)}/${encodeURIComponent(art.name)}`))}}catch{return {...c,value:null}}
  }));
  const gateFor=phase=>(t.gates||[]).find(g=>g.after_phase===phase);
  const phaseRows=t.phases.map(p=>{
   const price=priceOf(p.phase_key),credit=price?.rates?.scale==='credits'?`${credits(price.price)} credits · ${usd(price.price_usd)}`:price?'legacy scale':'-';
   return `<li><span>${esc(p.label||humanLabel(p.phase_key))}</span><span>${p.started_at&&p.completed_at?esc(durationLabel(p.completed_at-p.started_at)):'-'}</span><span>${credit}</span></li>`}).join('');
  const approved=t.phases.filter(p=>['approved','done'].includes(p.state)).length;
  const total=audit?.totals?`${credits(audit.totals.price)} credits · ${usd(audit.totals.price_usd)}`:'-';
  app.innerHTML=`<div class="complete-page"><div class="page-head compact-head"><div class="ch-left"><a class="back" href="/tasks/${id}/view">← Back to task</a><div class="eyebrow">Generation ${String(t.generation).padStart(2,'0')} · Complete</div></div><div class="ch-main"><h1>Congratulations, ${esc(t.title)} is complete</h1><p>${end?`Finished ${esc(new Date(end*1000).toLocaleString())}`:'Finished'}</p></div></div>
   <section class="panel complete-summary"><div class="audit-totals"><div><span>Total time</span><b>${start&&end?esc(durationLabel(end-start)):'-'}</b></div><div><span>Phases</span><b>${approved}/${t.phases.length}</b></div><div><span>Price</span><b>${total}</b></div></div>
   <div class="audit-totals complete-counts">${counts.map(c=>{const g=gateFor(c.phase),v=c.value===null?'-':c.value.toLocaleString();return `<div><span>${c.label}</span><b>${g?`<a href="/tasks/${id}/gates/${encodeURIComponent(g.gate_key)}">${v}</a>`:v}</b></div>`}).join('')}</div>
   <ol class="complete-phases">${phaseRows}</ol></section>
   <section class="panel"><a id="download-artifacts" class="primary button-link" download href="/api/tasks/${id}/artifacts.zip">Download artifacts</a>
   <h2>Final coding-agent package</h2><button id="prepare-package" class="primary">Prepare final package</button><div id="package-status" role="status"></div></section></div>`;
  confetti(app.querySelector('.complete-page'));
  const button=app.querySelector('#prepare-package'),status=app.querySelector('#package-status');
  button.onclick=async()=>{button.disabled=true;status.textContent='Checking outputs, decisions and traceability…';try{const result=await api(`/api/tasks/${id}/packages`,{method:'POST',body:'{}',timeout:null});status.innerHTML=`<p>Generation ${result.generation} · ready</p><a class="primary button-link" download href="${esc(result.url)}">Download coding-agent package</a><details><summary>Package contents</summary><ul>${(result.contents||[]).map(name=>`<li>${esc(name)}</li>`).join('')}</ul></details>`}catch(error){status.textContent=error.message;button.disabled=false}};
 }
 return {completePage};
}
