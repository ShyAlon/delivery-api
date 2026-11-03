// Shared operator navigation and row-menu infrastructure. No API authority.
export function mountTabs(host,root,tabs){
 const panels=new Set(tabs.flatMap(x=>x.panels));host.setAttribute('role','tablist');
 const buttons=tabs.map(tab=>{const button=document.createElement('button');button.textContent=tab.label;button.id='tab-'+tab.id;button.setAttribute('role','tab');button.setAttribute('aria-controls',tab.panels.join(' '));button.onclick=()=>select(tab.id,true);host.append(button);return button;});
 function select(id,update){if(activeMenuClose)activeMenuClose();const index=Math.max(0,tabs.findIndex(x=>x.id===id));tabs.forEach((tab,i)=>{buttons[i].classList.toggle('active',i===index);buttons[i].setAttribute('aria-selected',String(i===index));buttons[i].tabIndex=i===index?0:-1;});for(const id of panels){const panel=root.querySelector('#'+id);if(panel){panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby','tab-'+tabs.find(t=>t.panels.includes(id)).id);panel.hidden=!tabs[index].panels.includes(id);panel.classList.toggle('view-in',!panel.hidden);}}if(update)history.replaceState({},'','#'+tabs[index].id);}
 host.addEventListener('keydown',e=>{const index=buttons.indexOf(document.activeElement);if(index<0)return;let next=index;if(e.key==='ArrowRight')next=(index+1)%tabs.length;else if(e.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;else if(e.key==='Home')next=0;else if(e.key==='End')next=tabs.length-1;else return;e.preventDefault();select(tabs[next].id,true);buttons[next].focus();});
 window.addEventListener('hashchange',()=>select(location.hash.slice(1),false));select(location.hash.slice(1),false);
}
let activeMenuClose=null;
export function mountRowMenu(host,row,actions){
 const trigger=document.createElement('button');trigger.textContent='⋯';trigger.className='row-menu-trigger';trigger.setAttribute('aria-label','Job actions');trigger.setAttribute('aria-haspopup','menu');trigger.setAttribute('aria-expanded','false');host.append(trigger);let menu=null;
 function close(){if(menu)menu.remove();menu=null;if(activeMenuClose===close)activeMenuClose=null;trigger.setAttribute('aria-expanded','false');document.removeEventListener('pointerdown',outside);window.removeEventListener('resize',close);}
 function outside(e){if(menu&&!menu.contains(e.target)&&e.target!==trigger)close();}
 function open(x,y){if(activeMenuClose)activeMenuClose();close();activeMenuClose=close;menu=document.createElement('div');menu.className='operator-row-menu';menu.setAttribute('role','menu');actions.forEach(action=>{const button=document.createElement('button');button.setAttribute('role','menuitem');button.textContent=action.label;button.onclick=()=>{close();trigger.focus();action.run(trigger);};menu.append(button);});document.body.append(menu);menu.style.left=Math.max(8,Math.min(x,innerWidth-menu.offsetWidth-8))+'px';menu.style.top=Math.max(8,Math.min(y,innerHeight-menu.offsetHeight-8))+'px';trigger.setAttribute('aria-expanded','true');menu.firstElementChild.focus();menu.onkeydown=e=>{const items=[...menu.children],i=items.indexOf(document.activeElement);if(e.key==='Tab'){close();trigger.focus();}else if(e.key==='Escape'){e.preventDefault();close();trigger.focus();}else if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();items[e.key==='Home'?0:e.key==='End'?items.length-1:(i+(e.key==='ArrowDown'?1:items.length-1))%items.length].focus();}};document.addEventListener('pointerdown',outside);window.addEventListener('resize',close);}
 trigger.onclick=()=>{if(menu){close();return;}const rect=trigger.getBoundingClientRect();open(rect.left,rect.bottom+5);};row.tabIndex=0;row.oncontextmenu=e=>{e.preventDefault();open(e.clientX,e.clientY);};row.addEventListener('keydown',e=>{if(e.key==='ContextMenu'||(e.shiftKey&&e.key==='F10')){e.preventDefault();const rect=row.getBoundingClientRect();open(rect.left,rect.top);}});
}

// Shared accessible status treatment: native tooltip works on hover/focus;
// touch and keyboard users open the same explanation through the action.
export function statusPill(status,explanation,resolve){
 const node=document.createElement(resolve?'button':'span');node.className='operator-status operator-status-'+status;
 node.textContent=status==='fail'?'Blocked':status==='pass'?'Passed':'Needs approval';node.title=explanation;
 if(resolve){node.type='button';node.setAttribute('aria-label',node.textContent+': '+explanation);node.onclick=resolve;}
 return node;
}
