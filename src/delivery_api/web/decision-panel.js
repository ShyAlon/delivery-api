import {esc} from './client-core.js';
/* Shared pieces of the founder decision panel. Every decision page uses these so the same things look and behave the same:
   - a checklist that always says what still blocks the primary button (a button is never silently disabled),
   - a closed "Model" box for the ops-flavoured controls. */
export const modelPanelHtml=inner=>inner&&inner.trim()?`<details class="context-box model-panel"><summary>Model</summary><div class="context-body">${inner}</div></details>`:'';
export const checklistHtml=()=>`<div class="decision-checklist" id="decision-checklist" role="status" aria-live="polite"></div>`;
/* blockers: [{text, jump?:cssSelector}] - empty means the primary action is available. */
export function refreshDecision(root,blockers,{gated=['[data-action="approve"]','[data-action="edit"]']}={}){
 compactDecision(root);
 const box=root.querySelector('#decision-checklist');if(!box)return;
 box.classList.toggle('ready',!blockers.length);
 const readonly=blockers.some(b=>/past review.*read-only|not waiting for a decision/.test(b.text));
 root.dataset.decisionState=readonly?'readonly':'current';
 root.querySelector('.shared-action-strip')?.classList.toggle('readonly',readonly);
 box.innerHTML=blockers.length?`<b>Before you can continue</b><ul>${blockers.map((b,i)=>`<li>${b.jump?`<button type="button" class="link-button" data-jump="${esc(b.jump)}" data-i="${i}">${esc(b.text)}</button>`:esc(b.text)}</li>`).join('')}</ul>`:'<b>Ready to continue.</b>';
 if(readonly)box.innerHTML='<b>Past review - read-only.</b><p>Open the current review to make a decision.</p>';
 box.querySelectorAll('[data-jump]').forEach(a=>a.onclick=()=>{
  // The target can be in the evidence column, outside the controls form.
  // Keep lookup inside this decision page, not another modal/page instance.
  const page=root.closest('.gate-page,.decision-page')||root;
  let target;try{target=page.querySelector(a.dataset.jump)}catch{return}
  if(!target)return;
  if(target.tagName==='DETAILS')target.open=true;
  for(let parent=target.parentElement;parent&&parent!==page;parent=parent.parentElement)
   if(parent.tagName==='DETAILS')parent.open=true;
  target.querySelectorAll(':scope > details.context-box').forEach(detail=>detail.open=true);
  const focus=target.matches('input,select,textarea,button,a[href]')?target:
   target.querySelector('input:not([disabled]):not([type=hidden]),select:not([disabled]):not([aria-hidden=true]),textarea:not([disabled])')||target.querySelector('button:not([disabled]),a[href],summary');
  if(focus){
   for(let parent=focus.parentElement;parent&&parent!==page;parent=parent.parentElement)
    if(parent.tagName==='DETAILS')parent.open=true;
   focus.focus({preventScroll:true});focus.scrollIntoView({behavior:'smooth',block:'center'});
  }
  else{if(!target.hasAttribute('tabindex'))target.tabIndex=-1;target.focus({preventScroll:true});target.scrollIntoView({behavior:'smooth',block:'center'})}
 });
 for(const sel of gated)root.querySelectorAll(sel).forEach(b=>{b.disabled=root.getAttribute('aria-busy')==='true'||root.classList.contains('applied')||b.getAttribute('aria-busy')==='true'||blockers.length>0;b.setAttribute('aria-describedby','decision-checklist');if(blockers.length)b.title=blockers[0].text;else b.removeAttribute('title')});
}

/* One shared action owner. Original buttons stay inside their original form.
   No field wrapping, cloned controls, nested action drawers or hidden decisions. */
export function compactDecision(root){
 if(root.dataset.compactDecision||!root.matches('.gate-controls'))return;
 root.dataset.compactDecision='true';
 const strip=document.createElement('div');strip.className='shared-action-strip';strip.setAttribute('aria-label','Decision actions');
 const actions=root.querySelector('.gate-page-actions');if(actions)strip.append(actions);
 for(const node of [root.querySelector('#decision-checklist'),root.querySelector('#gate-decision-status')])if(node)strip.append(node);
 root.prepend(strip);
 const title=root.querySelector('h2');if(title)title.textContent='Your decision';
 const intro=root.querySelector('.dialog-intro');if(intro)intro.hidden=true;
}
