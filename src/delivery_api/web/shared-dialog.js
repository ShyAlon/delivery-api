/* One dialog lifecycle for native private pages and customer overlay content. */
import {logEnter,runAction} from './ui-action.js';
let sequence=0;
const bindings=new WeakMap();
export function bindDialog(dialog,options={}){
 if(!dialog)return;const old=bindings.get(dialog);if(old){Object.assign(old,options);return dialog}
 const state={returnTo:document.activeElement,...options};bindings.set(dialog,state);
 dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');
 dialog.querySelectorAll('label:has(input),label:has(select),label:has(textarea)').forEach(l=>l.classList.add('dialog-field'));
 const heading=dialog.querySelector('h1,h2,h3');if(heading&&!dialog.hasAttribute('aria-label')&&!dialog.hasAttribute('aria-labelledby')){heading.id||=`dialog-title-${++sequence}`;dialog.setAttribute('aria-labelledby',heading.id)}
 const close=()=>{if(state.canClose?.()===false||dialog.querySelector('[aria-busy="true"]'))return;if(state.close)state.close();else if(dialog.localName==='dialog')dialog.close();else dialog.closest('.dialog-layer')?.remove();state.onClose?.();state.returnTo?.isConnected&&state.returnTo.focus()};
 dialog.addEventListener('keydown',e=>{if(e.defaultPrevented)return;
  if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close();return}
  const items=[...dialog.querySelectorAll('button,input,select,textarea,a[href],[tabindex="0"]')].filter(x=>!x.disabled&&x.getClientRects().length),first=items[0],last=items.at(-1);
  if(e.key==='Tab'&&items.length&&((e.shiftKey&&document.activeElement===first)||(!e.shiftKey&&document.activeElement===last)||!dialog.contains(document.activeElement))){e.preventDefault();(e.shiftKey?last:first).focus()}
  if(e.key==='Enter'&&!e.isComposing&&(!e.target.matches('textarea,[contenteditable="true"]')||e.ctrlKey||e.metaKey)&&!e.target.matches('button,a,select')){const form=state.form||dialog.querySelector('form')||(dialog.localName==='form'?dialog:null),primary=state.primary||dialog.querySelector('[data-dialog-primary],button.primary,button[type="submit"]');if(form||primary){e.preventDefault();e.stopPropagation();if(form){logEnter('dialog','form.requestSubmit');form.requestSubmit()}else{logEnter('dialog','primary.click');primary.click()}}}
 });
 dialog.addEventListener('cancel',e=>{e.preventDefault();close()});
 requestAnimationFrame(()=>{const focus=typeof state.focus==='string'?dialog.querySelector(state.focus):state.focus; (focus||dialog.querySelector('[autofocus],input,select,textarea,button'))?.focus()});return dialog;
}
export function createDialog(title){const d=document.createElement('dialog');d.className='shared-dialog';const h=document.createElement('h2');h.textContent=title;d.append(h);document.body.append(d);d.addEventListener('close',()=>{const s=bindings.get(d);d.remove();s?.returnTo?.isConnected&&s.returnTo.focus()});bindDialog(d);return d}
export function dialogField(dialog,label,input){const row=document.createElement('label');row.className='dialog-field';const text=document.createElement('span');text.textContent=label;input.id||=`dialog-field-${++sequence}`;row.htmlFor=input.id;text.id=input.id+'-label';input.setAttribute('aria-labelledby',text.id);row.append(text,input);dialog.append(row);return input}
export function dialogActions(dialog,{label='Approve',run,busy='Working…',success='Saved',cancel='Cancel',focus='primary'}={}){const status=document.createElement('p');status.className='dialog-status';status.setAttribute('role','status');const row=document.createElement('div');row.className='dialog-actions';const no=document.createElement('button'),yes=document.createElement('button');no.textContent=cancel;yes.textContent=label;yes.className='primary';yes.dataset.dialogPrimary='true';no.onclick=()=>dialog.close();yes.onclick=()=>runAction('dialog:'+dialog.querySelector('h2')?.textContent,[yes,no],async()=>{status.className='dialog-status';status.textContent=busy;try{await run();status.textContent=success;status.classList.add('success')}catch(e){status.textContent=e.message||String(e);status.classList.add('error');throw e}},{busyLabel:busy}).catch(()=>{});row.append(no,yes);dialog.append(status,row);bindDialog(dialog,{primary:yes,focus:focus==='cancel'?no:yes});return {status,primary:yes,cancel:no}}
export function observeDialogs(host,close){const scan=()=>host.querySelectorAll('[role="dialog"],[role="alertdialog"],.dialog,.decision-dialog,.requirement-dialog').forEach(d=>{if(bindings.has(d))return;bindDialog(d,{close:()=>{const layer=d.closest('.dialog-layer');layer?layer.remove():close()}})});new MutationObserver(scan).observe(host,{childList:true,subtree:true});scan()}
