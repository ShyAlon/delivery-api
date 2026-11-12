import {esc} from './client-core.js';

/* Shared gate-page presentation: Title Case headings, tooltip pills, collapsible boxes. Display only. */
const SMALL=new Set(['a','an','and','as','at','but','by','for','in','nor','of','on','or','the','to','vs','via','with']);
export function titleCase(text){
 return String(text).replace(/_/g,' ').split(/(\s+)/).map((w,i,all)=>{
  if(!w.trim())return w;
  const first=all.slice(0,i).every(x=>!x.trim()),last=all.slice(i+1).every(x=>!x.trim());
  if(/[A-Z]{2,}|\d/.test(w)||w.includes('/'))return w;
  const lower=w.toLowerCase();
  if(!first&&!last&&SMALL.has(lower))return lower;
  return lower.charAt(0).toUpperCase()+lower.slice(1)}).join('');
}
/* Run after a page renders: headings without child markup become Title Case. Item titles built from founder
   content (data-keep) are left exactly as written. */
export function titleCaseHeadings(root){
 root.querySelectorAll('h1,h2,h3').forEach(el=>{if(el.children.length||el.closest('[data-keep-case]')||el.closest('.semantic-item'))return;el.textContent=titleCase(el.textContent)});
}
/* Radio pills with a tooltip and a caption. options: [{value,label,tip}] */
export function pillGroup({name,legend,options,checked='',attrs=''}){
 return `<fieldset class="decision-pills" role="radiogroup" ${attrs}><legend>${esc(legend)}</legend>${options.map(o=>`<label class="pill" title="${esc(o.tip)}"><input type="radio" name="${esc(name)}" value="${esc(o.value)}" ${String(o.value)===String(checked)?'checked':''}><span>${esc(o.label)}</span></label>`).join('')}</fieldset>`;
}
export function boxHtml(title,bodyHtml,{open=false}={}){return `<details class="context-box" ${open?'open':''}><summary>${esc(title)}</summary><div class="context-body">${bodyHtml}</div></details>`}

const TIPS={'':'Make a choice for this item.',remove:'Remove this item from the deliverable.',replace:'Replace it with your own wording, shown below.',accept:'Keep it as written and accept the flag as a known exception.'};
/* Progressive enhancement: a short <select> becomes radio pills with tooltips. The select stays in the form
   (hidden), so names, saved drafts and submit code keep working. */
export function pillifySelects(root,{max=4}={}){
 root.querySelectorAll('select:not([data-no-pills])').forEach(sel=>{
  if(sel.dataset.pilled)return;const opts=[...sel.options];if(opts.length>max)return;
  sel.dataset.pilled='1';sel.classList.add('pilled-select');sel.tabIndex=-1;sel.setAttribute('aria-hidden','true');
  const group=document.createElement('div');group.className='decision-pills';group.setAttribute('role','radiogroup');
  const uid='p'+Math.random().toString(36).slice(2,8);
  const render=()=>{group.innerHTML=opts.filter(o=>o.value!==''||opts.length<3).map(o=>`<label class="pill" title="${esc(o.title||TIPS[o.value]||o.textContent)}"><input type="radio" name="${uid}" value="${esc(o.value)}" ${sel.value===o.value?'checked':''} ${sel.disabled||o.disabled?'disabled':''}><span>${esc(o.textContent==='Choose'?'None Yet':titleCase(o.textContent))}</span></label>`).join('')};
  render();sel.after(group);
  group.addEventListener('change',e=>{sel.value=e.target.value;sel.dispatchEvent(new Event('change',{bubbles:true}))});
  sel._sync=render;new MutationObserver(render).observe(sel,{attributes:true,attributeFilter:['disabled']});
 });
}
export function syncPills(root){root.querySelectorAll('select.pilled-select').forEach(s=>s._sync&&s._sync())}
/* Artifact cards become closed collapsible boxes (first one stays open). */
export function collapseCards(host){
 [...host.querySelectorAll(':scope > .gate-artifact')].forEach((card,i)=>{
  if(card.dataset.collapsed||card.id==='semantic-panel')return;const h=card.querySelector(':scope > h2, :scope > h3');if(!h)return;
  card.dataset.collapsed='1';const d=document.createElement('details');d.className='context-box';if(i===0)d.open=true;
  /* The document's own title goes in the header row next to the chevron (no empty row, no repeated heading). A trailing record UUID is kept as a tooltip only. */
  const s=document.createElement('summary');const doc=card.querySelector('.artifact-readable h1');const raw=(doc?doc.textContent:h.textContent).trim();
  const m=raw.match(/^(.*?)\s*[-\u2013\u2014:]\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i);
  const t=m?m[1].trim():raw;s.textContent=t;if(m)s.title=m[2];s.setAttribute('aria-label',t);if(doc)doc.remove();h.remove();const b=document.createElement('div');b.className='context-body';while(card.firstChild)b.append(card.firstChild);d.append(s,b);card.append(d)});
}

/* "What am I deciding?" block. Factual copy only. Shown at the top of every gate page. */
export const GATE_COPY={
 semantic_review:{phase:'Intent Interview',asks:'Some of your answers were flagged as unclear, contradictory or unsupported. Nothing was deleted or rewritten. For each flagged item, say what should happen to it.',choices:[['Remove','Drops the item from the intent. The later phases will not use it.'],['Replace','You supply new wording. It is checked and then used instead of the original.'],['Accept Override','Keeps the item exactly as you wrote it and records that you accepted the flag.']],next:'Next: Apply Decisions and Continue records your choices, then the ConOps draft is prepared.'},
 interview_approval:{phase:'Intent and ConOps Review',asks:'Approve the intent and concept of operations as written, or send them back for rework. Approving does not answer the open inputs listed below; they stay flagged.',choices:[['Continue','Approves this intent and ConOps and moves on to the requirements phase.'],['Request Rework','Sends the ConOps back to be rebuilt using your guidance. Nothing moves on.']],next:''},
 conops_concept:{phase:'Concept of Operations',asks:'Pick the operating concept the product will be built around, and say why. The other concepts are dropped from the plan.',choices:[['Choose and Continue','Records your concept and reason, then the ConOps is completed around it.']],next:''}
};
export function explainerHtml(key,overrides={}){
 const c={...(GATE_COPY[key]||{}),...overrides};if(!c.asks)return'';
 return boxHtml('What You Are Deciding Here',`<p><b>Phase:</b> ${esc(c.phase)}</p><p><b>Decision:</b> ${esc(c.asks)}</p>${(c.choices||[]).length?`<ul>${c.choices.map(([a,b])=>`<li><b>${esc(a)}:</b> ${esc(b)}</li>`).join('')}</ul>`:''}${c.next?`<p>${esc(c.next)}</p>`:''}`,{open:false}).replace('class="context-box"','class="explainer-box"');
}
