import {esc} from './client-core.js';
/* Shared pieces of the founder decision panel. Every decision page uses these so the same things look and behave the same:
   - a checklist that always says what still blocks the primary button (a button is never silently disabled),
   - a closed "Model" box for the ops-flavoured controls. */
export const modelPanelHtml=inner=>inner&&inner.trim()?`<details class="context-box model-panel"><summary>Model</summary><div class="context-body">${inner}</div></details>`:'';
export const checklistHtml=()=>`<div class="decision-checklist" id="decision-checklist" role="status" aria-live="polite"></div>`;
/* blockers: [{text, jump?:cssSelector}] - empty means the primary action is available. */
export function refreshDecision(root,blockers,{gated=['[data-action="approve"]','[data-action="edit"]']}={}){
 const box=root.querySelector('#decision-checklist');if(!box)return;
 box.classList.toggle('ready',!blockers.length);
 box.innerHTML=blockers.length?`<b>Before you can continue</b><ul>${blockers.map((b,i)=>`<li>${b.jump?`<button type="button" class="link-button" data-jump="${esc(b.jump)}" data-i="${i}">${esc(b.text)}</button>`:esc(b.text)}</li>`).join('')}</ul>`:'<b>Ready to continue.</b>';
 box.querySelectorAll('[data-jump]').forEach(a=>a.onclick=()=>{const t=document.querySelector(a.dataset.jump);t?.scrollIntoView({behavior:'smooth',block:'start'});t?.focus?.()});
 for(const sel of gated)root.querySelectorAll(sel).forEach(b=>{b.disabled=blockers.length>0;b.setAttribute('aria-describedby','decision-checklist');if(blockers.length)b.title=blockers[0].text;else b.removeAttribute('title')});
}
