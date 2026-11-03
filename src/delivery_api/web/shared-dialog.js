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
 const close=()=>{if(state.canClose?.()===false||(!state.allowBusyClose&&dialog.querySelector('[aria-busy="true"]')))return;if(state.close)state.close();else if(dialog.localName==='dialog')dialog.close();else dialog.closest('.dialog-layer')?.remove();state.onClose?.();state.returnTo?.isConnected&&state.returnTo.focus()};
 dialog.addEventListener('keydown',e=>{if(e.defaultPrevented)return;
  if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close();return}
  const items=[...dialog.querySelectorAll('button,input,select,textarea,a[href],[tabindex="0"]')].filter(x=>!x.disabled&&x.getClientRects().length),first=items[0],last=items.at(-1);
  if(e.key==='Tab'&&items.length&&((e.shiftKey&&document.activeElement===first)||(!e.shiftKey&&document.activeElement===last)||!dialog.contains(document.activeElement))){e.preventDefault();(e.shiftKey?last:first).focus()}
  if(e.key==='Enter'&&!e.isComposing&&(!e.target.matches('textarea,[contenteditable="true"]')||e.ctrlKey||e.metaKey)&&!e.target.matches('button,a,select')){const form=state.form||dialog.querySelector('form')||(dialog.localName==='form'?dialog:null),primary=state.primary||dialog.querySelector('[data-dialog-primary],button.primary,button[type="submit"]');if(form||primary){e.preventDefault();e.stopPropagation();if(form){logEnter('dialog','form.requestSubmit');form.requestSubmit()}else{logEnter('dialog','primary.click');primary.click()}}else logEnter('dialog','none: no form or primary action')}
 });
 dialog.addEventListener('cancel',e=>{e.preventDefault();close()});
 requestAnimationFrame(()=>{const focus=typeof state.focus==='string'?dialog.querySelector(state.focus):state.focus; (focus||dialog.querySelector('[autofocus],input,select,textarea,button'))?.focus()});return dialog;
}
export function createDialog(title){const d=document.createElement('dialog');d.className='shared-dialog';const h=document.createElement('h2');h.textContent=title;d.append(h);document.body.append(d);d.addEventListener('close',()=>{const s=bindings.get(d);d.remove();s?.returnTo?.isConnected&&s.returnTo.focus()});bindDialog(d);return d}
export function dialogField(dialog,label,input){const row=document.createElement('label');row.className='dialog-field';const text=document.createElement('span');text.textContent=label;input.id||=`dialog-field-${++sequence}`;row.htmlFor=input.id;text.id=input.id+'-label';input.setAttribute('aria-labelledby',text.id);row.append(text,input);dialog.append(row);return input}
export function dialogActions(dialog,{label='Approve',run,busy='Saving changes. Usually a few seconds.',success='Saved',cancel='Cancel',focus='primary',estimateSeconds=10}={}){
 const status=document.createElement('p');status.className='dialog-status';status.setAttribute('role','status');
 const row=document.createElement('div');row.className='dialog-actions';const no=document.createElement('button'),yes=document.createElement('button');
 no.textContent=cancel;yes.textContent=label;yes.setAttribute('aria-label',label);yes.className='primary';yes.dataset.dialogPrimary='true';no.onclick=()=>dialog.close();
 yes.onclick=()=>runAction('dialog:'+dialog.querySelector('h2')?.textContent,yes,async()=>{
  status.className='dialog-status';const started=performance.now();const update=()=>{const elapsed=Math.floor((performance.now()-started)/1000);status.textContent=busy+' '+elapsed+'s elapsed.'+(elapsed>estimateSeconds?' Taking longer than estimated. You may close this view; closing does not undo a submitted change.':' Estimated '+Math.max(0,estimateSeconds-elapsed)+'s remaining (not guaranteed).')};update();const timer=setInterval(update,1000);
  try{await run();status.textContent=success;status.classList.add('success')}catch(e){status.textContent=e.message||String(e);status.classList.add('error');throw e}finally{clearInterval(timer)}
 },{reason:busy}).catch(()=>{});
 const note=document.createElement('p');note.className='dialog-cancel-note';note.textContent='Cancel closes this view. It does not undo a change already submitted.';
 row.append(no,yes);dialog.append(status,note,row);bindDialog(dialog,{primary:yes,focus:focus==='cancel'?no:yes,allowBusyClose:true});return {status,primary:yes,cancel:no};
}
export function observeDialogs(host,close){const scan=()=>host.querySelectorAll('[role="dialog"],[role="alertdialog"],.dialog,.decision-dialog,.requirement-dialog').forEach(d=>{if(bindings.has(d))return;bindDialog(d,{close:()=>{const layer=d.closest('.dialog-layer');layer?layer.remove():close()}})});new MutationObserver(scan).observe(host,{childList:true,subtree:true});scan()}

export function commandDialog(title,explanation,commands){
 const dialog=createDialog(title),note=document.createElement('p');note.textContent=explanation;dialog.append(note);
 for(const command of commands){const input=dialogField(dialog,'Owner terminal command',document.createElement('textarea'));input.readOnly=true;input.rows=2;input.value=command;const copy=document.createElement('button');copy.textContent='Copy command';copy.onclick=async()=>{try{await navigator.clipboard.writeText(command);copy.textContent='Copied';}catch{input.focus();input.select();copy.textContent='Select and copy command';}};dialog.append(copy);}
 const safe=document.createElement('p');safe.textContent='Nothing runs from this dialog. Review terminal output before any --apply confirmation. No paid approval or activation is made.';dialog.append(safe);
 const close=document.createElement('button');close.textContent='Close';close.dataset.dialogPrimary='true';close.onclick=()=>dialog.close();dialog.append(close);bindDialog(dialog,{primary:close,focus:close});dialog.showModal();return dialog;
}
