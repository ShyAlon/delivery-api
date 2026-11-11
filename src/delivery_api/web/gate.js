import {activationAllowed} from './pointer-activation.js';
import {Page} from './page-component.js';
import {Dialog} from './shared-dialog.js';
import {runAction} from './ui-action.js';
import {api,esc,reveal} from './client-core.js';
import {onPageExit,pageEpoch,pageStale} from './page-lifecycle.js';
import {explainerHtml,titleCase,titleCaseHeadings,pillifySelects,syncPills,collapseCards} from './gate-ui.js';
import {markdownHtml} from './format.js';
import {gateStructured,gateSummary} from './gate-presenters.js';
import {validationPanelHtml} from './validation-panel.js';
import {modelPanelHtml,checklistHtml,refreshDecision} from './decision-panel.js';
import {replaceFieldsHtml,readReplacement,fillReplacement} from './semantic-review-ui.js';
const app=document.querySelector('#app'),modal=document.querySelector('#modal');
const hideIds=v=>Array.isArray(v)?v.map(hideIds):v&&typeof v==='object'?Object.fromEntries(Object.entries(v).filter(([k])=>!/(^|_)(id|ids|ref|refs)$/.test(k)).map(([k,x])=>[k,hideIds(x)])):v;
export function createGate(ctx){
const refreshCurrent=(...args)=>ctx.refreshCurrent(...args);
const bindDialogKeys=(...args)=>ctx.bindDialogKeys(...args);
const helpTip=(...args)=>ctx.helpTip(...args);
const getAuthState=(...args)=>ctx.getAuthState(...args);
function gateUrl(taskId,gateKey,revision){return `/tasks/${encodeURIComponent(taskId)}/gates/${encodeURIComponent(gateKey)}`+(revision?`?revision=${encodeURIComponent(revision)}`:"")}
function bindGates(tasks){document.querySelectorAll('.gate').forEach(el=>{const t=tasks.find(x=>x.task_id===el.dataset.task),g=t?.gates.find(x=>x.gate_key===el.dataset.gate);if(!g)return;el.setAttribute('role','link');el.setAttribute('aria-label',`Review ${g.label}`);const open=e=>{e.stopPropagation();if(!activationAllowed(e))return;location.href=gateUrl(t.task_id,g.gate_key)};el.onclick=open;el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open(e)}}});}
function showReplay(t){Dialog.open(modal,Dialog.html(`<div class="eyebrow">Recorded evidence · zero subprocesses</div><h2>Replay this run</h2><label>Playback<select name="speed"><option value="100">Accelerated · 100×</option><option value="0">Instant</option></select></label><div class="notice">Closed gates are real pauses. The replay waits at the halo until a decision is recorded.</div><div class="actions"><button type="button" class="secondary" id="cancel">Cancel</button><button class="primary">Start replay</button></div>`,{"tag":"form","attributes":{"class":"dialog translucent","id":"replayform"}}));document.querySelector('#cancel').onclick=()=>modal.classList.add('hidden');bindDialogKeys({form:document.querySelector('#replayform'),focus:'select[name="speed"]'});document.querySelector('#replayform').onsubmit=async e=>{e.preventDefault();let speed=Number(new FormData(e.target).get('speed'));await runAction(`task.replay:${t.task_id}`,e.target.querySelector('button.primary'),async()=>{await api(`/api/tasks/${t.task_id}/simulate`,{method:'POST',body:JSON.stringify({speed})});await refreshCurrent(t.task_id)});};}
let gateDispose=()=>{};
async function gatePage(taskId,gateKey){
 gateDispose();gateDispose=()=>{};
 const at=pageEpoch();
 const query=new URLSearchParams(location.search),data=await api(`/api/tasks/${taskId}/gates/${encodeURIComponent(gateKey)}/review?${query}`),review=data.review,t=review.task,g=t.gates.find(g=>g.gate_key===gateKey);
 history.replaceState({},'',gateUrl(taskId,gateKey,review.revision)+(query.get('draft')?'&draft='+encodeURIComponent(query.get('draft')):'')+(query.get('item')?'&item='+encodeURIComponent(query.get('item')):''));
 if(!g){app.innerHTML=`<a class="back" href="/tasks/${esc(taskId)}/view">← Back to task</a><div class="notice">Gate not found.</div>`;return}
 const {routes}=await api('/api/model-routes'),phase=g.after_phase,phaseArtifacts=(t.artifacts||[]).filter(a=>a.phase_key===phase&&!a.stale),artifacts=phaseArtifacts.filter(a=>a.present!==false&&a.valid!==false);
 const nextPhase=t.phases.findIndex(p=>p.phase_key===phase)+1,next=t.phases[nextPhase],configured=t.phase_config||{};
 const target=next?.phase_key,defaultChoice=target&&configured[target]?.executor==='generator'?await api(`/api/tasks/${taskId}/generator-default/${target}`):null,redoChoice=configured[phase]?.executor==='generator'?await api(`/api/tasks/${taskId}/generator-default/${phase}`):null,defaultModel=defaultChoice?.model,redoModel=redoChoice?.model;
 const options=routes.flatMap(route=>(route.models||[route.model?.split(':').slice(1).join(':')]).filter(Boolean).map(model=>({value:`${route.id}:${model}`,available:route.available})));
 const modelPicker=(name,label,model)=>model?`<label>${label}<small class="gate-model-resolved" data-model-for="${name}">${esc((name==='redo_model'?redoChoice:defaultChoice)?.source==='legacy generator configuration (no recorded exact selection)'?'Legacy generator fallback':'Current task default')}: ${esc(model)}</small><select name="${name}"><option value="">Use current task default</option>${options.map(o=>`<option value="${esc(o.value)}" ${o.available?'':'disabled'}>${esc(o.value)}${o.available?'':' · unavailable'}</option>`).join('')}</select></label>`:(name==='next_model'&&defaultChoice?.source==='selection required'||name==='redo_model'&&redoChoice?.source==='selection required'?`<label>${label}<small class="gate-model-resolved" data-model-for="${name}">No recorded exact model. Choose one before continuing.</small><select name="${name}" required><option value="">Choose a model</option>${options.map(o=>`<option value="${esc(o.value)}" ${o.available?'':'disabled'}>${esc(o.value)}${o.available?'':' · unavailable'}</option>`).join('')}</select></label>`:'');const modelSelect=modelPicker('next_model','Next phase model',defaultModel)+modelPicker('redo_model','Redo model',redoModel);
 if(pageStale(at))return;   // navigated away while loading: never commit
 app.innerHTML=`<div class="gate-page">${Page.heading(`<div class="ch-left"><a class="back" href="/tasks/${esc(taskId)}/view">← Back to ${esc(t.title)}</a><div class="eyebrow">Interactive Gate</div></div><div class="ch-main"><h1>${esc(titleCase(g.label))}</h1><p>Review the ${esc(t.phases.find(p=>p.phase_key===phase)?.label||phase)} result before deciding.</p></div><a id="gate-permalink" href="${esc(location.pathname+location.search)}">Link to this gate review</a>${data.actionable?'':data.historical?'<div class="notice">Historical gate review - read-only. <a href="'+gateUrl(taskId,gateKey)+'">Open current gate</a></div>':'<div class="notice">Current gate review - not waiting for a decision.</div>'}`,{classes:"gate-page-head page-head compact-head"})}<div class="gate-page-grid"><section class="gate-evidence">${explainerHtml("gate",{phase:t.phases.find(p=>p.phase_key===phase)?.label||phase,asks:`Review the ${t.phases.find(p=>p.phase_key===phase)?.label||phase} result below. Approving it lets the task move on to ${next?.label||"the next step"}.`,choices:[["Approve","Approves the result as shown (with the option you picked, if any) and starts "+(next?.label||"the next step")+"."],["Save Edit","Stores your edited decision text without moving on."],["Ask For Changes","Regenerates this phase using your note. Nothing moves on."]]})}<div class="eyebrow">Evidence for Approval</div><h2>${esc(t.phases.find(p=>p.phase_key===phase)?.label||phase)}</h2><div id="gate-artifacts" aria-live="polite">${artifacts.length?'Loading current artifacts…':'No current valid artifact is available for this gate. Approval is unavailable until the artifact appears.'}</div></section><form id="gateform" class="gate-controls"><h2>Decision</h2><div class="gate-status ${esc(g.state)}">${g.is_open?'Open · passes automatically':g.state==='waiting'?'Waiting here · approval paused':'Closed · will pause here'}</div>${g.options.filter((x,i)=>i===0||!/^choose another option$/i.test(x)).length>1?`<label>Choose an option<select name="selected_option">${g.options.map(x=>`<option>${esc(x)}</option>`).join('')}</select></label>`:`<input type="hidden" name="selected_option" value="${esc(g.options[0]||'')}">`}<label>Edit the decision<textarea name="edited_text" placeholder="Write the approved decision in your own words"></textarea></label><label>Ask for changes: what should change?<textarea name="steer" placeholder="What specifically should regeneration change?"></textarea></label>${modelPanelHtml(`<label class="switch"><input id="gateopen" type="checkbox" ${g.is_open?'checked':''}> Open future arrivals ${helpTip('What does opening a gate do?','An open gate auto-approves when the phase arrives. It does not approve the current waiting decision.')}</label><div id="gatestatus" class="field-status" aria-live="polite"></div>${modelSelect}`)}<label>Comment / annotate<textarea name="comment" maxlength="7000"></textarea></label><button type="button" id="gate-save-comment" class="secondary">Save comment only</button><button type="button" id="gate-save-draft" class="secondary">Save draft</button><div id="gate-comments">${(data.comments||[]).map(c=>`<p><b>${esc(c.actor)}</b>: ${esc(c.comment)}</p>`).join('')}</div>${checklistHtml()}<p class="dialog-intro">Approve moves on to ${esc(next?.label||'the next step')}. Save edit records your wording. Ask for changes regenerates this phase with your note and marks downstream results stale.</p><div class="gate-page-actions"><button type="button" class="secondary" data-action="redo">Ask for changes</button><button type="button" class="secondary" data-action="edit">Save edit</button><button type="button" class="primary" data-action="approve">Approve ${esc(t.phases.find(p=>p.phase_key===phase)?.label||phase)}</button></div><div id="gate-decision-status" aria-live="polite"></div></form></div></div>`;
 if(data.closed_revision_draft){
  const proposed=document.createElement('section');proposed.className='panel closed-revision-draft';
  proposed.innerHTML='<h2>Propose a revision</h2><p>This saved approval remains unchanged. Describe what regeneration should change in this phase. Saving a proposal does not edit artifacts, make downstream results stale, or restart work.</p><p class="closed-revision-cost">Committing this change will regenerate this phase and affected downstream work, require new approvals, and incur extra cost. You will review that cost before committing. Regenerate and restart is not available yet.</p><label>What should regeneration change?<textarea id="closedrevisiontext" maxlength="20000"></textarea></label><button class="secondary" id="saveclosedrevision">Save proposed revision</button><p id="closedrevisionstatus" role="status"></p>';
  app.querySelector('.gate-evidence').append(proposed);
  if(data.draft?.kind==='closed_gate_revision')proposed.querySelector('#closedrevisiontext').value=data.draft.edited_text||'';
  proposed.querySelector('#saveclosedrevision').onclick=async()=>runAction(`gate.revision:${taskId}:${gateKey}`,proposed.querySelector('#saveclosedrevision'),async()=>{
   const text=proposed.querySelector('#closedrevisiontext').value.trim(),status=proposed.querySelector('#closedrevisionstatus'),button=proposed.querySelector('#saveclosedrevision');
   if(!text){status.textContent='Describe what regeneration should change first.';return}
   try{const saved=await api(`/api/tasks/${taskId}/gates/${encodeURIComponent(gateKey)}/revision-drafts`,{method:'POST',body:JSON.stringify({review_revision:review.revision,edited_text:text})});history.replaceState({},'',gateUrl(taskId,gateKey,review.revision)+'&draft='+encodeURIComponent(saved.draft_id));document.querySelector('#gate-permalink').href=location.pathname+location.search;status.textContent='Proposal saved. The approved result and downstream work are unchanged.'}
   catch(error){status.textContent=error.message}
  });
 }
 const actionable=data.actionable;const form=document.querySelector('#gateform');form.onsubmit=e=>e.preventDefault();const host=document.querySelector('#gate-artifacts');
 let currentArtifacts=artifacts,currentPhaseArtifacts=phaseArtifacts,renderedArtifactKey=null,undecidedItems=0,undecidedStack=0,staleNote='',artifactFailures=[],gateCommitted=false,semanticSaved=false,stackSaved=false;
 function updateDecision(){
   const blockers=[];
   if(gateCommitted)blockers.push({text:'Decision saved. Opening task progress.'});
   const requiredModel=form.elements.next_model;
   if(requiredModel?.required&&!requiredModel.value)blockers.push({text:'Choose the next phase model before approving.',jump:'.model-panel'});
   if(!data.actionable)blockers.push({text:data.historical?'This is a past review and is read-only.':'This gate is not waiting for a decision.'});
   if(staleNote)blockers.push({text:staleNote});
   for(const failure of artifactFailures)blockers.push({text:failure,jump:'#gate-artifacts'});
   if(!currentArtifacts.length)blockers.push({text:'There is no current artifact to approve yet.'});
   if(undecidedStack)blockers.push({text:`${undecidedStack} architecture decision${undecidedStack===1?'':'s'} still open. Decide each one, then save.`,jump:'#stack-panel'});
   if(undecidedItems)blockers.push({text:`${undecidedItems} flagged input${undecidedItems===1?'':'s'} still need${undecidedItems===1?'s':''} a decision. Choose for each, then press Apply item decisions.`,jump:'#semantic-panel'});
   refreshDecision(form,blockers);
 }
 function missingGateEvidence(){
   const invalid=currentPhaseArtifacts.filter(a=>a.present!==false&&a.valid===false);
   if(invalid.length){
     const details=invalid.map(a=>`${a.name}: ${(a.validation_errors||[]).join('; ')||a.validation_error||'artifact failed validation'}`).join(' | ');
     return `Error: The current artifact failed validation.\nLikely cause: ${details}\nNext: Check the task artifact and server log; do not approve or rerun until the cause is fixed.`;
   }
   const missing=currentPhaseArtifacts.filter(a=>a.present===false);
   return `Error: No current valid artifact is available for this gate.\nLikely cause: ${missing.length?missing.map(a=>a.name).join(', ')+' is missing from the artifact folder.':'No current artifact was found for this phase.'}\nNext: Check the task artifact folder and server log; do not approve or rerun until the artifact is available.`;
 }
 async function renderGateArtifacts(){
   const key=JSON.stringify(currentPhaseArtifacts.map(a=>[a.name,a.generation,a.ref,a.present,a.valid,a.validation_errors]));
   if(key===renderedArtifactKey)return;
   renderedArtifactKey=key;
   artifactFailures=[];
   updateDecision();
   if(!currentArtifacts.length){host.innerHTML=`<div class="notice gate-evidence-error">${esc(missingGateEvidence())}</div>`;return}
if(currentArtifacts.length){host.innerHTML='';reveal(host);for(const a of currentArtifacts){let card=document.createElement('article');card.className='gate-artifact';host.append(card);try{let retained=review.documents.find(d=>d.name===a.name);if(!retained?.view)throw new Error(retained?.error||'Retained artifact missing');let data=retained.view,content=data.content||'',structured=data.format==='json'?JSON.parse(content):null,summary=structured?gateSummary(structured):content.split(/\n+/).map(x=>x.trim()).filter(x=>x&&!/^#/.test(x)).slice(0,4).join(' ').slice(0,750);card.innerHTML=`<h3>${esc(data.title||a.title||a.name)}</h3>${a.name==='architecture.json'?`<div class="decision-provenance"><h4>Decision sources</h4>${(data.decision_provenance||[]).map(item=>`<div class="decision-source"><b>${esc(item.decision_id||item.title||'Decision')}</b><span>${esc(item.label)}</span>${item.evidence_status&&item.evidence_status.availability!=='available'?`<small>Supplemental founder provenance ${esc(item.evidence_status.availability)} (${esc(item.evidence_status.reason)}). The primary decision is preserved; founder origin is not established.</small>`:''}${item.founder_evidence?.length?`<small>Interview turn(s): ${esc(item.founder_evidence.flatMap(x=>x.source_turns).join(', '))}</small>`:'<small>No matching founder technology preference found. Review this model choice.</small>'}${item.baseline_refs?.length?`<small>Existing-system refs: ${esc(item.baseline_refs.join(', '))}</small>`:''}</div>`).join('')||'<p>Decision source not available for this artifact.</p>'}</div>`:''}${a.name==='validation.json'&&data.validation_panel?validationPanelHtml(data.validation_panel):data.format==='markdown'?`<article class="artifact-readable">${markdownHtml(content)}</article>`:structured?`<article class="gate-structured">${gateStructured(structured)}</article>`:`<pre class="artifact-content">${esc(content)}</pre>`}${actionable?`<a class="button-link" download href="/api/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(phase)}/${encodeURIComponent(a.name)}">Download ${esc(a.name)}</a>`:'Historical content retained above; the live download may be a newer revision.'}` }catch(e){card.innerHTML=`<div class="notice">${esc(a.name)} could not be loaded: ${esc(e.message)}. Approval is unavailable.</div>`;artifactFailures.push(`${a.name} could not be shown. Reload a complete review before approving.`);updateDecision()}}}
 }
 await renderGateArtifacts();
 const semantic=review.semantic_review;
 const flagTitle=t=>{const w=String(t||'').trim().split(/\s+/).filter(Boolean);return w.length>32?w.slice(0,32).join(' ')+'…':(w.join(' ')||'Flagged Item')};
 if(semantic?.findings?.length){
  const resolved=new Set((semantic.decisions||[]).map(d=>d.item_id)),items=[...new Map(semantic.findings.map(f=>[f.item_id,f])).values()];
  const panel=document.createElement('section');panel.className='gate-artifact';panel.id='semantic-panel';
  panel.innerHTML=`<h2>Flagged Inputs</h2><p>Nothing was deleted or rewritten. Review every flagged item before approving. Accept overrides meaning, not contract structure.</p>${items.map((f,i)=>`<article><h3>${esc(flagTitle(semantic.findings.find(x=>x.item_id===f.item_id)?.reason))}</h3>${semantic.findings.filter(x=>x.item_id===f.item_id).filter(x=>String(x.reason||'').trim().split(/\s+/).length>32).map(x=>`<p>${esc(x.reason)}</p>`).join('')}<details class="context-box" open><summary>The Item As Written</summary><div class="context-body gate-structured">${gateStructured(hideIds(f.original))}</div></details><label>Decision<select name="semantic-action-${i}" required><option value="">Choose</option><option value="remove">Remove</option><option value="replace">Replace</option>${semantic.findings.some(x=>x.item_id===f.item_id&&x.kind==='structural')?'':'<option value="accept">Accept override</option>'}</select></label>${replaceFieldsHtml(i,f.original)}</article>`).join('')}<button type="button" id="apply-semantic">Apply item decisions</button><div id="semantic-status" role="status"></div>`;
  host.append(panel);
  if(query.get('item')&&!items.some(f=>f.item_id===query.get('item')))throw new Error('Unknown flagged item');
  const syncReplace=()=>items.forEach((_,i)=>panel.querySelector(`.replace-fields[data-for="${i}"]`)?.classList.toggle('hidden',panel.querySelector(`[name="semantic-action-${i}"]`).value!=='replace'));panel.addEventListener('change',syncReplace);
  for(const d of data.draft?.semantic_decisions||semantic.decisions||[]){const i=items.findIndex(f=>f.item_id===d.item_id);if(i>=0){panel.querySelector(`[name="semantic-action-${i}"]`).value=d.action||'';{let saved=d.replacement;if(saved===undefined&&d.replacement_text){try{saved=JSON.parse(d.replacement_text)}catch{saved=undefined}}if(saved!==undefined)fillReplacement(panel,i,saved)}}}
  syncReplace();
  if(query.get('item'))panel.querySelectorAll('article')[items.findIndex(f=>f.item_id===query.get('item'))]?.scrollIntoView();

  undecidedItems=items.filter(f=>!resolved.has(f.item_id)).length;updateDecision();
  panel.querySelector('#apply-semantic').onclick=async()=>runAction(`gate.semantic:${taskId}:${gateKey}`,panel.querySelector('#apply-semantic'),async()=>{const status=panel.querySelector('#semantic-status');try{
    const decisions=items.map((f,i)=>{const action=panel.querySelector(`[name="semantic-action-${i}"]`).value;if(!action)throw new Error('Choose for every flagged item.');return {item_id:f.item_id,action,...(action==='replace'?{replacement:readReplacement(panel,i,f.original)}:{})}});
    status.textContent='Validating item decisions…';
    await api(`/api/tasks/${taskId}/gates/${encodeURIComponent(gateKey)}/semantic-review`,{method:'POST',body:JSON.stringify({revision:semantic.revision,decisions})});
    semanticSaved=true;status.innerHTML=`Decisions saved. <a href="${gateUrl(taskId,gateKey)}">Open reviewed gate</a>`;
  }catch(error){status.textContent=error.message;if(error.status===409){staleNote='This review changed. Reload before deciding.';updateDecision()}}}).finally(()=>{panel.querySelector('#apply-semantic').disabled=semanticSaved||!!staleNote});
  if(!data.actionable)panel.querySelectorAll('textarea,select,button').forEach(c=>c.disabled=true);
 }

 const stack=review.stack_decisions;
 if(stack?.slots?.length){
  const slotTitle=k=>titleCase(String(k||'').replace(/[_-]+/g,' ').trim());
  const panel=document.createElement('section');panel.className='gate-artifact';panel.id='stack-panel';
  const decidedCount=()=>stack.slots.filter(s=>s.founder_decision).length;
  panel.innerHTML=`<h2>Technical Choices</h2>${stack.slots.map((s,i)=>{
   const d=s.founder_decision||{},f=s.technical_choice,chosen=d.text||f?.default_value||'';
   const options=f?.options||[],selected=options.find(o=>o.value===chosen);
   return `<article class="stack-slot" data-slot="${i}"><h3>${esc(f?.title||slotTitle(s.key))}</h3>${f?`<p>${esc(f.purpose)}</p><div class="stack-default"><small>${d.text?'Your Choice':'Default'}</small><strong>${esc(selected?.label||chosen)}</strong><span class="stack-technology">${esc(chosen)}</span></div><p>${esc(d.reason||f.reason)}</p>${selected?`<p class="stack-tradeoff">${esc(selected.implication)}</p>`:''}<small class="stack-research">Research Pending</small>`:`<p class="stack-missing">${d.text?esc(d.text):'No Proposed Default Yet'}</p>`}<details class="stack-change"><summary>Change</summary>${s.prefill&&!s.founder_decision?'<p class="stack-prefill">Earlier Answer. Confirm Or Change.</p>':''}<div class="stack-options" role="group">${(options.length?options:(s.options||[]).map(o=>({value:o,label:o,implication:''}))).map((o,j)=>`<button type="button" class="stack-option" data-slot="${i}" data-value="${esc(o.value)}">${esc(o.label)}</button>${o.implication?`<small>${esc(o.implication)}</small>`:''}`).join('')}</div><label>Your Choice<textarea name="stack-text-${i}" rows="2" maxlength="2000">${esc(d.text||s.prefill?.text||'')}</textarea></label><label>Your Reason (Optional)<input name="stack-reason-${i}" maxlength="1000" value="${esc(d.reason||s.prefill?.reason||'')}"></label><button type="button" class="stack-save secondary" data-slot="${i}">Save Change</button></details></article>`;
  }).join('')}<span id="stack-status" role="status"></span>`;
  host.append(panel);
  const refreshCount=()=>{undecidedStack=stack.slots.filter(s=>!s.founder_decision&&!s.technical_choice).length;updateDecision()};refreshCount();
  panel.addEventListener('click',async e=>{
   const option=e.target.closest('.stack-option');
   if(option){const i=option.dataset.slot;panel.querySelector(`[name="stack-text-${i}"]`).value=option.dataset.value;return}
   const save=e.target.closest('.stack-save');if(!save)return;
   const status=panel.querySelector('#stack-status'),i=save.dataset.slot;
   await runAction(`gate.stack:${taskId}:${gateKey}`,save,async()=>{try{
    const text=panel.querySelector(`[name="stack-text-${i}"]`).value.trim(),reason=panel.querySelector(`[name="stack-reason-${i}"]`).value.trim();
    if(!text)throw new Error('Your change needs a choice.');
    status.textContent='Saving…';
    await api(`/api/tasks/${taskId}/gates/${encodeURIComponent(gateKey)}/stack-decisions`,{method:'POST',body:JSON.stringify({revision:stack.revision,decisions:[{key:stack.slots[i].key,text,reason}]})});
    stackSaved=true;status.innerHTML=`Change Saved. <a href="${gateUrl(taskId,gateKey)}">Open Reviewed Gate</a>`;
    // Saved choice changed gate evidence. Review it rather than approve an old revision.
    staleNote='Your saved change updated the evidence. Open the reviewed gate before deciding.';updateDecision();form.querySelectorAll('[data-action]').forEach(b=>b.disabled=true);
   }catch(error){status.textContent=error.message;if(error.status===409){staleNote='This review changed. Reload before deciding.';updateDecision()}}}).finally(()=>{save.disabled=stackSaved||!!staleNote});
  });
  if(!data.actionable)panel.querySelectorAll('textarea,input,button').forEach(c=>c.disabled=true);
 }
 const saved=data.draft||{};for(const [name,value] of Object.entries({selected_option:saved.choice,edited_text:saved.rationale,comment:saved.comment}))if(value&&form.elements[name])form.elements[name].value=value;
 const polish=()=>{collapseCards(host);pillifySelects(document.querySelector('.gate-page'));syncPills(document.querySelector('.gate-page'));titleCaseHeadings(document.querySelector('.gate-controls')||document)};polish();new MutationObserver(()=>{polish()}).observe(host,{childList:true});
 document.querySelector('#gate-save-comment').onclick=async()=>runAction(`gate.comment:${taskId}:${gateKey}`,document.querySelector('#gate-save-comment'),async()=>{const comment=form.elements.comment.value.trim();if(!comment)return;try{await api(`/api/tasks/${taskId}/gates/${encodeURIComponent(gateKey)}/comments`,{method:'POST',body:JSON.stringify({revision:review.revision,comment})});document.querySelector('#gate-comments').insertAdjacentHTML('beforeend',`<p>${esc(comment)}</p>`);form.elements.comment.value='';document.querySelector('#gate-decision-status').textContent='Comment attached. No decision or regeneration changed.'}catch(e){document.querySelector('#gate-decision-status').textContent=e.message}});
 document.querySelector('#gate-save-draft').onclick=async()=>runAction(`gate.draft:${taskId}:${gateKey}`,document.querySelector('#gate-save-draft'),async()=>{try{const saved=await api(`/api/tasks/${taskId}/gates/${encodeURIComponent(gateKey)}/drafts`,{method:'POST',body:JSON.stringify({revision:review.revision,choice:form.elements.selected_option.value,rationale:form.elements.edited_text.value,comment:form.elements.comment.value,semantic_decisions:(semantic?.findings||[]).filter((f,i,a)=>a.findIndex(x=>x.item_id===f.item_id)===i).map((f,i)=>({item_id:f.item_id,action:host.querySelector(`[name="semantic-action-${i}"]`)?.value||'',replacement_text:host.querySelector(`[name="semantic-replacement-${i}"]`)?.value||''}))})});history.replaceState({},'',gateUrl(taskId,gateKey,review.revision)+'&draft='+encodeURIComponent(saved.draft_id)+(query.get('item')?'&item='+encodeURIComponent(query.get('item')):''));document.querySelector('#gate-permalink').href=location.pathname+location.search;document.querySelector('#gate-decision-status').textContent='Draft saved. This link restores it.'}catch(e){document.querySelector('#gate-decision-status').textContent=e.message}});
 if(!data.actionable)form.querySelectorAll('input,textarea,select,button').forEach(c=>c.disabled=true);
 let gateRefreshPending=false,gateRefreshTimer=null;
 async function refreshGateEvidence(){
   if(!data.actionable||gateRefreshPending||!host.isConnected)return;
   gateRefreshPending=true;
   try{
     const live=await api(`/api/tasks/${taskId}`);
     const control=live.control_snapshot;
     const current=live.gates.find(item=>item.gate_key===gateKey);
     if(control&&control.mode==='waiting_gate'&&control.wait_id!==gateKey)
       throw new Error('A different gate owns the current wait; reload before deciding');
     if(!current||current.state!=='waiting'){
       // Keep unsaved form edits intact. The server will reject a stale action.
       document.querySelector('#gate-decision-status').textContent='This gate changed. Reload to see its current state.';
       staleNote='This gate changed. Reload to see its current state.';updateDecision();
       return;
     }
     const updated=await api(`/api/tasks/${taskId}/gates/${encodeURIComponent(gateKey)}/review`);
     if(updated.review.revision!==review.revision){document.querySelector('#gate-decision-status').textContent='Gate evidence changed. Reload the current gate before deciding.';staleNote='The gate evidence changed. Reload the current gate before deciding.';updateDecision();return}
     g.revision=current.revision;
     currentPhaseArtifacts=(live.artifacts||[]).filter(a=>a.phase_key===phase&&!a.stale);
     currentArtifacts=currentPhaseArtifacts.filter(a=>a.present!==false&&a.valid!==false);
     await renderGateArtifacts();
   }catch(e){document.querySelector('#gate-decision-status').textContent=`Could not refresh gate evidence: ${e.message}`}
   finally{gateRefreshPending=false}
 }
 onPageExit(()=>gateEvents.close());const gateEvents=new EventSource(`/api/tasks/${taskId}/stream`);
 gateEvents.addEventListener('progress',ev=>{
   if(!host.isConnected){gateEvents.close();return}
   const event=JSON.parse(ev.data);
   if(event.phase_key===phase&&['complete','gate','artifact','progress'].includes(event.kind)){
     clearTimeout(gateRefreshTimer);gateRefreshTimer=setTimeout(refreshGateEvidence,150)
   }
 });
 // Files may appear after the last task event or a transport reconnect;
 // refresh without forcing the founder to lose unsaved gate choices.
 onPageExit(()=>clearInterval(gateRefreshInterval));const gateRefreshInterval=setInterval(()=>{
   if(!host.isConnected){gateDispose();return}
   refreshGateEvidence();
 },5000);
 gateDispose=()=>{clearInterval(gateRefreshInterval);clearTimeout(gateRefreshTimer);gateEvents.close?.()};
 form.querySelectorAll('select[name=next_model],select[name=redo_model]').forEach(select=>select.onchange=()=>{form.querySelector(`[data-model-for="${select.name}"]`).textContent=select.value?`Selected override: ${select.value}`:(select.required?'No recorded exact model. Choose one before continuing.':`${(select.name==='redo_model'?redoChoice:defaultChoice)?.source==='legacy generator configuration (no recorded exact selection)'?'Legacy generator fallback':'Current task default'}: ${select.name==='redo_model'?redoModel:defaultModel}`);updateDecision()});const open=document.querySelector('#gateopen');open.onchange=async()=>runAction(`gate.toggle:${taskId}:${gateKey}`,open,async()=>{let requested=open.checked;try{let updated=await api(`/api/tasks/${taskId}/gates/${encodeURIComponent(gateKey)}/toggle`,{method:'POST',body:JSON.stringify({is_open:requested})});g.is_open=Boolean(updated.is_open);g.revision=updated.revision;open.checked=g.is_open;document.querySelector('#gatestatus').textContent=g.is_open?'Gate opened.':'Gate closed.'}catch(e){open.checked=!requested;document.querySelector('#gatestatus').textContent=e.message}});
 document.querySelectorAll('[data-action]').forEach(button=>button.onclick=async()=>runAction(`gate.decide:${taskId}:${gateKey}`,Array.from(form.querySelectorAll('[data-action]')),async()=>{const form=document.querySelector('#gateform'),d=Object.fromEntries(new FormData(form)),chosen=button.dataset.action==='redo'?d.redo_model:d.next_model;delete d.redo_model;delete d.next_model;if(!chosen&&(button.dataset.action==='redo'?redoChoice:defaultChoice)?.source==='selection required'){document.querySelector('#gate-decision-status').textContent='Choose a model before continuing.';return}if(chosen){let [model_route,...model]=chosen.split(':');d.model_route=model_route;d.model=model.join(':')}d.action=button.dataset.action;d.expected_revision=g.revision;d.review_revision=review.revision;if(d.action==='redo'&&!String(d.steer||'').trim()){document.querySelector('#gate-decision-status').textContent='a redo needs a reason: describe what should change.';return}document.querySelector('#gate-decision-status').innerHTML='<span class="waiting-spinner" aria-hidden="true"></span> Saving decision and starting the next step…';try{await api(`/api/tasks/${taskId}/gates/${encodeURIComponent(gateKey)}/decide`,{method:'POST',body:JSON.stringify(d)});gateCommitted=true;sessionStorage.setItem(`phase-transition:${taskId}`,JSON.stringify({from:phase,target:d.action==='redo'?phase:t.phases[t.phases.findIndex(p=>p.phase_key===phase)+1]?.phase_key||null,started:Date.now()}));location.href=`/tasks/${encodeURIComponent(taskId)}/view`}catch(e){if(e.status===409){staleNote='This decision changed. Reload the current review before deciding.';updateDecision()}else{form.querySelectorAll('[data-action]').forEach(b=>b.disabled=false);updateDecision()}document.querySelector('#gate-decision-status').innerHTML=`<div class="notice">${esc(e.message)}</div>`}}).finally(updateDecision));
}


return {gateUrl, bindGates, showReplay, gatePage};
}
