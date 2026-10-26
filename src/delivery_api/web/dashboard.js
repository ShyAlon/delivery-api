import {api,esc,reveal} from './client-core.js';
import {navigate} from './spa-router.js';
import {pageEpoch,pageStale} from './page-lifecycle.js';
import {runAction} from './ui-action.js';
import {vmap,stateLabel} from './vmap.js';
const app=document.querySelector('#app'),modal=document.querySelector('#modal');
export function createDashboard(ctx){
const closeModal=(...args)=>ctx.closeModal(...args);
const openDialog=(...args)=>ctx.openDialog(...args);
const refreshCurrent=(...args)=>ctx.refreshCurrent(...args);
const bindDialogKeys=(...args)=>ctx.bindDialogKeys(...args);
const showError=(...args)=>ctx.showError(...args);
const detail=(...args)=>ctx.detail(...args);
const bindGates=(...args)=>ctx.bindGates(...args);
let taskSort='updated-desc',taskFilter='';
async function dashboard(){
 const at=pageEpoch();
 app.innerHTML=`<section class="task-tools"><label>Filter tasks<input type="search" disabled aria-hidden="true" tabindex="-1"></label><label>Sort<select disabled aria-hidden="true" tabindex="-1"></select></label><button class="primary" disabled aria-hidden="true" tabindex="-1">+ New task</button></section><section class="task-grid" aria-busy="true"><p class="task-empty loading" role="status">Loading tasks\u2026</p>${'<article class="task task-skeleton" aria-hidden="true"><span class="sk sk-top"></span><span class="sk sk-title"></span><span class="sk sk-meta"></span><span class="sk sk-graph"></span><span class="sk sk-foot"></span></article>'.repeat(3)}</section>`;
 const {tasks}=await api('/api/tasks');
 if(pageStale(at))return;   // navigated away while loading: never commit
 const render=()=>{
  const q=taskFilter.trim().toLowerCase();let shown=tasks.filter(t=>!q||t.title.toLowerCase().includes(q)||(t.brief||'').toLowerCase().includes(q)||stateLabel(t.state).toLowerCase().includes(q));
  shown.sort((a,b)=>taskSort==='name-asc'?a.title.localeCompare(b.title):taskSort==='name-desc'?b.title.localeCompare(a.title):taskSort==='created-asc'?a.created_at-b.created_at:b.updated_at-a.updated_at);
  document.querySelector('.task-grid').innerHTML=shown.map(t=>`<article class="task" data-open="${esc(t.task_id)}"><div class="top"><span class="state ${t.state}"><i class="pulse"></i>${stateLabel(t.state)}</span><span><button class="card-action rename" data-id="${esc(t.task_id)}">Rename</button><button class="card-action delete" data-id="${esc(t.task_id)}">Delete</button></span></div><h2>${esc(t.title)}</h2><div class="meta">${esc(t.brief||'No brief yet')}</div>${vmap(t.phases,t.gates,t.task_id)}<div class="foot"><span>${t.phases.filter(p=>['approved','done'].includes(p.state)).length} / ${t.phases.length} approved phases</span><span>${t.artifact_count} artifacts →</span></div></article>`).join('')||`<p class="task-empty">${q?'No tasks match this filter.':'No tasks yet. Create one to begin.'}</p>`;
  document.querySelectorAll('[data-open]').forEach(el=>el.onclick=()=>navigate(`/tasks/${el.dataset.open}/view`));
  document.querySelectorAll('.rename').forEach(el=>el.onclick=e=>{e.stopPropagation();showRename(tasks.find(t=>t.task_id===el.dataset.id))});
  document.querySelectorAll('.delete').forEach(el=>el.onclick=e=>{e.stopPropagation();showDelete(tasks.find(t=>t.task_id===el.dataset.id))});
  bindGates(shown);
 };
 app.innerHTML=`<section class="task-tools"><label>Filter tasks<input id="taskfilter" type="search" placeholder="Name, brief, or state" value="${esc(taskFilter)}"></label><label>Sort<select id="tasksort"><option value="updated-desc">Recently updated</option><option value="name-asc">Name A-Z</option><option value="name-desc">Name Z-A</option><option value="created-asc">Oldest first</option></select></label><button class="primary" id="new">+ New task</button></section><section class="task-grid"></section>`;
 document.querySelector('#new').onclick=showNew;
 document.querySelector('#taskfilter').oninput=e=>{taskFilter=e.target.value;render()};document.querySelector('#tasksort').value=taskSort;document.querySelector('#tasksort').onchange=e=>{taskSort=e.target.value;render()};render();
 reveal(app.querySelector('.task-grid'));
}

function showRename(t){modal.classList.remove('hidden');modal.innerHTML=`<form class="dialog" id="renameform"><div class="eyebrow">Rename task</div><h2>${esc(t.title)}</h2><p class="dialog-intro">Renaming changes the workspace label only. It does not rerun phases or change artifacts.</p><label>Unique task name<input name="title" required maxlength="120" value="${esc(t.title)}"></label><div class="actions"><button type="button" class="secondary" id="cancel">Cancel</button><button class="primary">Save name</button></div></form>`;document.querySelector('#cancel').onclick=()=>modal.classList.add('hidden');bindDialogKeys({form:document.querySelector('#renameform'),focus:'input[name="title"]'});document.querySelector('#renameform').onsubmit=async e=>{e.preventDefault();try{await api(`/api/tasks/${t.task_id}`,{method:'PATCH',body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});await refreshCurrent();}catch(x){showError(x)}};}
function showDelete(t){
 const dialog=openDialog(`<div class="dialog" role="dialog" aria-label="Delete task ${esc(t.title)}"><div class="eyebrow">Delete task</div><h2>${esc(t.title)}</h2><div class="notice">This permanently removes the task, phases, gates, events, and artifact references from the UI. Original files in the artifacts folder stay on disk.</div><div class="actions"><button class="secondary" id="cancel">Cancel</button><button class="primary" id="confirmdelete">Delete task</button></div></div>`,{focus:'#cancel',canClose:()=>!deleting});
 const cancel=dialog.querySelector('#cancel'),confirm=dialog.querySelector('#confirmdelete');let deleting=false;
 cancel.onclick=()=>{if(!deleting)closeModal()};
 confirm.onclick=async()=>{
  if(deleting)return;deleting=true;confirm.disabled=true;cancel.disabled=true;confirm.textContent='Deleting…';
  try{await api(`/api/tasks/${t.task_id}`,{method:'DELETE'});if(location.pathname.startsWith('/tasks/')){closeModal();navigate('/');return}await refreshCurrent()}
  catch(x){deleting=false;confirm.disabled=false;cancel.disabled=false;confirm.textContent='Delete task';showError(x)}
 };
}

function showNew(){modal.classList.remove('hidden');modal.innerHTML=`<form class="dialog" id="newform"><div class="eyebrow">New definition task</div><h2>What are we building?</h2><p class="dialog-intro">Create the durable task first. Starting the interview is a separate action, so you can review the brief before any model work begins.</p><label>Task name<input name="title" required placeholder="Astronomy explorer for curious kids"></label><label>Starting idea<textarea name="brief" placeholder="Describe the product idea in a sentence or two…"></textarea></label><div class="actions"><button type="button" class="secondary" id="cancel">Cancel</button><button class="primary">Create empty task</button></div></form>`;document.querySelector('#cancel').onclick=()=>modal.classList.add('hidden');bindDialogKeys({form:document.querySelector('#newform'),focus:'input[name="title"]'});document.querySelector('#newform').onsubmit=async e=>{e.preventDefault();let d=Object.fromEntries(new FormData(e.target));try{await runAction('task.create',e.target.querySelector('button.primary'),async()=>{let t=await api('/api/tasks',{method:'POST',body:JSON.stringify(d)});location.href=`/tasks/${t.task_id}/view`;},{busyLabel:'Creating…',reason:'Creating the task - one moment',keepDisabledOnSuccess:true})}catch(x){showError(x)}};}
function openRouteSwitch(taskId,task,execution,routes){
 let busy=false,current=task.model_route||execution.route||'unknown',currentProvider=current.split(/[/:]/)[0],
  currentModel=(execution.effective_models?.probing||[])[1]||null;
 const choices=routes.flatMap(route=>(route.models||[route.model?.split(':').slice(1).join(':')]).filter(Boolean).map(model=>({route:route.id,model,available:route.available,
  label:`${route.id}:${model}`,error:route.error}))).filter(choice=>choice.route!==currentProvider||choice.model!==currentModel);
 const available=choices.filter(choice=>choice.available);
 let dialog=openDialog(`<div class="dialog translucent route-switch-dialog" role="dialog" aria-label="Switch interview model"><button type="button" class="dialog-x" aria-label="Close model switch">×</button><div class="eyebrow">EXPLICIT MODEL CHANGE · NEXT TURN</div><h2>Switch interview model?</h2><p class="dialog-intro">Your interview stays intact. Only calls after your next answer use the new model. The change appears in your transcript, route history and cost report.</p><p>Current: <b>${esc(currentModel?`${currentProvider}:${currentModel}`:current)}</b></p><label>New model<select id="route-switch-target">${choices.map(choice=>`<option value="${esc(choice.label)}" ${choice.available?'':'disabled'}>${esc(choice.label)}${choice.available?'':` · unavailable: ${esc(choice.error||'preflight failed')}`}</option>`).join('')}</select></label><p class="route-switch-price">Ollama runs locally at $0 API cost. DeepSeek is billed by measured tokens at the configured price table; the total is not known in advance.</p><p>Switch only while the interview waits for input. If another answer or switch arrives first, refresh and try again.</p><div class="actions"><button type="button" class="secondary" id="cancel-switch">Back</button><button type="button" class="primary" id="confirm-switch" ${available.length?'':'disabled'}>Confirm switch</button></div><div class="field-status" id="switch-status" aria-live="polite"></div></div>`,{focus:'#route-switch-target',canClose:()=>!busy});
 const cancel=dialog.querySelector('#cancel-switch'),confirm=dialog.querySelector('#confirm-switch'),select=dialog.querySelector('#route-switch-target'),status=dialog.querySelector('#switch-status');
 if(available[0])select.value=available[0].label;
 cancel.onclick=()=>{if(!busy)closeModal()};
 confirm.onclick=async()=>{if(busy||!select.value)return;busy=true;confirm.disabled=true;cancel.disabled=true;select.disabled=true;status.textContent='Saving switch…';
  try{let snapshot=await api(`/api/tasks/${taskId}/engine-snapshot`);if(!snapshot.open_input)throw new Error('The interview is no longer waiting for input. Refresh the task.');
   const choice=choices.find(x=>x.label===select.value),key=crypto.randomUUID();
   await api(`/api/tasks/${taskId}/model-route`,{method:'POST',body:JSON.stringify({model_route:choice.route,model:choice.model,expected_sequence:snapshot.sequence,expected_input_id:snapshot.open_input.input_id,idempotency_key:key})});
   closeModal();await detail(taskId);
  }catch(e){status.textContent=e.message;busy=false;confirm.disabled=false;cancel.disabled=false;select.disabled=false}
 };
}


return {dashboard, showRename, showDelete, showNew, openRouteSwitch};
}
