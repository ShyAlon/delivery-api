export {mountTabs} from './operator-tabs.js';
let activeMenuClose=null;
document.addEventListener('operator-navigation',()=>activeMenuClose?.());
export function mountRowMenu(host,row,actions){
 const trigger=document.createElement('button');trigger.textContent='⋯';trigger.className='row-menu-trigger';trigger.setAttribute('aria-label','Job actions');trigger.setAttribute('aria-haspopup','menu');trigger.setAttribute('aria-expanded','false');host.append(trigger);let menu=null;
 function close(){if(menu)menu.remove();menu=null;if(activeMenuClose===close)activeMenuClose=null;trigger.setAttribute('aria-expanded','false');document.removeEventListener('pointerdown',outside);window.removeEventListener('resize',close);}
 function outside(e){if(menu&&!menu.contains(e.target)&&e.target!==trigger)close();}
 function open(x,y){if(activeMenuClose)activeMenuClose();close();activeMenuClose=close;menu=document.createElement('div');menu.className='operator-row-menu';menu.setAttribute('role','menu');actions.forEach(action=>{const button=document.createElement('button');button.setAttribute('role','menuitem');button.textContent=action.label;button.onclick=()=>{close();trigger.focus();action.run(trigger);};menu.append(button);});document.body.append(menu);menu.style.left=Math.max(8,Math.min(x,innerWidth-menu.offsetWidth-8))+'px';menu.style.top=Math.max(8,Math.min(y,innerHeight-menu.offsetHeight-8))+'px';trigger.setAttribute('aria-expanded','true');menu.firstElementChild.focus();menu.onkeydown=e=>{const items=[...menu.children],i=items.indexOf(document.activeElement);if(e.key==='Tab'){close();trigger.focus();}else if(e.key==='Escape'){e.preventDefault();close();trigger.focus();}else if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();items[e.key==='Home'?0:e.key==='End'?items.length-1:(i+(e.key==='ArrowDown'?1:items.length-1))%items.length].focus();}};document.addEventListener('pointerdown',outside);window.addEventListener('resize',close);}
 trigger.onclick=()=>{if(menu){close();return;}const rect=trigger.getBoundingClientRect();open(rect.left,rect.bottom+5);};row.tabIndex=0;row.oncontextmenu=e=>{e.preventDefault();open(e.clientX,e.clientY);};row.addEventListener('keydown',e=>{if(e.key==='ContextMenu'||(e.shiftKey&&e.key==='F10')){e.preventDefault();const rect=row.getBoundingClientRect();open(rect.left,rect.top);}});
}

// Shared accessible status treatment: native tooltip works on hover/focus;
// touch and keyboard users open the same explanation through the action.
export function statusPill(status,explanation,resolve,label){
 const node=document.createElement(resolve?'button':'span');node.className='operator-status operator-status-'+status;
 node.textContent=label||(status==='fail'?'Blocked':status==='pass'?'Passed':'Needs approval');node.title=explanation;
 if(resolve){node.type='button';node.setAttribute('aria-label',node.textContent+': '+explanation);node.onclick=resolve;}
 return node;
}

export function statePill(label,reason='Stored state, not proof of worker liveness.'){
 const text=String(label),key=text.toLowerCase();
 const failed=['failed','error','needs attention','blocked','refused'],passed=['done','completed','resolved','current','fresh','approved'];
 const status=failed.includes(key)?'fail':passed.includes(key)?'pass':'unverified';
 return statusPill(status,reason,null,text);
}
