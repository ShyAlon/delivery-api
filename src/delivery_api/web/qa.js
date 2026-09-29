import {api,esc} from './client-core.js';
const app=document.querySelector('#app'),modal=document.querySelector('#modal');
export function createQa(ctx){
const debugOn=(...args)=>ctx.debugOn(...args);
const closeModal=(...args)=>ctx.closeModal(...args);
const openDialog=(...args)=>ctx.openDialog(...args);
const showError=(...args)=>ctx.showError(...args);
let feedbackState=new Map(),qaContext={taskId:null,turns:[]};
const setFeedbackState=rows=>{feedbackState=new Map(rows.map(r=>[fbKey(r.target,r.input_ref),r]))};
const setClassificationState=value=>{classificationState=value};
const setQaContext=(taskId,turns)=>{qaContext={taskId,turns}};
function fbKey(target,ref){return `${target}:${ref}`}
function qaActionsHtml(target,ref){
 const row=feedbackState.get(fbKey(target,ref)),up=row?.thumb===1,down=row?.thumb===-1,q=target==='question';
 return `<span class="qa-actions" data-target="${target}" data-ref="${ref}"><button type="button" class="qa-thumb${up?' active':''}" data-thumb="1" aria-pressed="${up}" title="${q?'Good question':'Interpreted well'}">👍</button><button type="button" class="qa-thumb${down?' active':''}" data-thumb="-1" aria-pressed="${down}" title="${q?'Bad question - flag it':'Bad interpretation - flag as misclassification'}">👎</button><button type="button" class="qa-comment-btn${row?.comment?' has-comment':''}" title="Comment on this ${q?'question':'answer'}">💬</button></span>`;
}
function qaNotesHtml(target,ref){
 const rows=[...feedbackState.values()].filter(r=>r.target===target&&r.input_ref===ref&&r.comment);
 if(!rows.length)return '';
 return `<div class="qa-notes" data-for="${target}:${ref}">${rows.map(r=>`<button type="button" class="qa-note" data-fid="${r.id}" title="Edit comment - ${esc(r.actor)}">${esc(r.comment)}</button>`).join('')}</div>`;
}
function questionNoteParts(question,ref){
 const notes=[...feedbackState.values()].filter(r=>r.target==='question'&&r.input_ref===ref&&r.comment);
 if(!notes.length)return [12,0];
 const questionLength=String(question).trim().length;
 const noteLength=notes.reduce((total,row)=>total+row.comment.trim().length,0);
 const questionParts=Math.max(5,Math.min(9,Math.round(12*questionLength/(questionLength+noteLength))));
 return [questionParts,12-questionParts];
}
let classificationState={};
function differedChip(side,key){
 if(!side?.differed)return '';
 const j=side.jev?.[key]??'no verdict',b=side.baseline?.[key]??'no verdict';
 return `<span class="qa-chip differed" title="Jev read: ${esc(String(j))} - engine used: ${esc(String(b))}">engine differed</span>`;
}
function qaChipsHtml(aref){
 const c=classificationState[aref];
 if(!c||!debugOn())return '';
 let chips=[];
 if(c.intent){const label=c.intent.jev?.intent||c.intent.baseline?.intent;if(label)chips.push(`<span class="qa-chip">intent · ${esc(String(label))}</span>`);chips.push(differedChip(c.intent,'intent'))}
 if(c.coverage){const label=c.coverage.jev?.relevance||c.coverage.baseline?.relevance;if(label)chips.push(`<span class="qa-chip">relevance · ${esc(String(label))}</span>`);chips.push(differedChip(c.coverage,'relevance'))}
 return chips.filter(Boolean).length?`<div class="qa-chips">${chips.join('')}</div>`:'';
}
function wireQaNotes(scope){
 scope.querySelectorAll('.qa-note').forEach(b=>b.onclick=()=>{const row=[...feedbackState.values()].find(r=>r.id===Number(b.dataset.fid));if(row)openFeedbackDialog(row.target,row.input_ref,row.content)});
}
function wireQaActions(el){
 const target=el.dataset.target,ref=el.dataset.ref,turn=qaContext.turns.find(t=>(target==='question'?t.qref:t.aref)===ref),content=target==='question'?turn?.q:turn?.a;
 el.querySelectorAll('.qa-thumb').forEach(b=>b.onclick=()=>sendThumb(el,target,ref,content,Number(b.dataset.thumb)));
 el.querySelector('.qa-comment-btn').onclick=()=>openFeedbackDialog(target,ref,content);
}
function refreshQaRow(el){
 const target=el.dataset.target,ref=el.dataset.ref,item=el.closest('.qa-item');
 if(!item)return;
 el.outerHTML=qaActionsHtml(target,ref);
 item.querySelector(`.qa-notes[data-for="${target}:${ref}"]`)?.remove();
 const fresh=item.querySelector(`.qa-actions[data-target="${target}"][data-ref="${ref}"]`);
 if(target==='question'){
  const row=fresh.closest('.qa-question'),question=row.querySelector('p').textContent;
  const [questionParts,noteParts]=questionNoteParts(question,ref);
  row.style.setProperty('--question-parts',questionParts);
  row.style.setProperty('--note-parts',noteParts);
  row.insertAdjacentHTML('beforeend',qaNotesHtml(target,ref));
 }else fresh.closest('.qa-answer').insertAdjacentHTML('afterend',qaNotesHtml(target,ref));
 wireQaActions(fresh);wireQaNotes(item);
}
async function sendThumb(el,target,ref,content,value){
 const key=fbKey(target,ref),existing=feedbackState.get(key);
 try{
  if(existing&&existing.thumb===value){
   if(existing.comment){const row=await api(`/api/tasks/${qaContext.taskId}/feedback/${existing.id}`,{method:'PATCH',body:JSON.stringify({thumb:null,comment:existing.comment})});feedbackState.set(key,row)}
   else{await api(`/api/tasks/${qaContext.taskId}/feedback/${existing.id}`,{method:'DELETE'});feedbackState.delete(key)}
  }else{
   const row=await api(`/api/tasks/${qaContext.taskId}/feedback`,{method:'POST',body:JSON.stringify({input_ref:ref,target,content:content||'',thumb:value,comment:existing?.comment||null})});
   feedbackState.set(key,row);
  }
  refreshQaRow(el);
 }catch(e){showError(e)}
}
function openFeedbackDialog(target,ref,content){
 const key=fbKey(target,ref),existing=feedbackState.get(key),q=target==='question';
 const dialog=openDialog(`<div class="dialog feedback-dialog" role="dialog" aria-label="${q?'Comment on this question':'Comment on this answer'}"><button class="dialog-x" aria-label="Close comment">×</button><div class="eyebrow">${q?'QUESTION FEEDBACK':'ANSWER FEEDBACK'} · ANNOTATION ONLY</div><h3>${q?'What should we learn from this question?':'What did the engine misread?'}</h3><p class="feedback-context">${esc(content||'')}</p><textarea id="feedbacktext" rows="4" maxlength="4000" placeholder="A note for the team - it does not steer the interview">${esc(existing?.comment||'')}</textarea><div class="dialog-actions"><button class="primary" id="savefeedback">Save comment</button>${existing?'<button class="secondary danger" id="deletefeedback">Delete feedback</button>':''}</div><div id="feedbackstatus" aria-live="polite"></div></div>`);
 if(!dialog)return;
 const text=dialog.querySelector('#feedbacktext'),status=dialog.querySelector('#feedbackstatus'),save=dialog.querySelector('#savefeedback');
 save.onclick=async()=>{
  const comment=text.value.trim();
  if(!comment&&!existing?.thumb){status.textContent='Add a comment or a thumb first.';return}
  save.disabled=true;save.textContent='Saving…';
  try{
   const row=await api(`/api/tasks/${qaContext.taskId}/feedback`,{method:'POST',body:JSON.stringify({input_ref:ref,target,content:content||'',thumb:existing?.thumb||null,comment:comment||null})});
   feedbackState.set(key,row);closeModal();
   const el=document.querySelector(`.qa-actions[data-target="${target}"][data-ref="${ref}"]`);if(el)refreshQaRow(el);
  }catch(e){status.textContent=e.message;save.disabled=false;save.textContent='Save comment'}
 };
 if(existing)dialog.querySelector('#deletefeedback').onclick=async e=>{
  const b=e.target;
  if(!b.dataset.armed){b.dataset.armed='1';b.textContent='Confirm delete?';setTimeout(()=>{if(b.isConnected){delete b.dataset.armed;b.textContent='Delete feedback'}},3000);return}
  try{await api(`/api/tasks/${qaContext.taskId}/feedback/${existing.id}`,{method:'DELETE'});feedbackState.delete(key);closeModal();const el=document.querySelector(`.qa-actions[data-target="${target}"][data-ref="${ref}"]`);if(el)refreshQaRow(el)}catch(err){showError(err)}
 };
}
async function contentRef(text){
 try{let buf=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(String(text).trim()));return [...new Uint8Array(buf)].map(b=>b.toString(16).padStart(2,'0')).join('').slice(0,16)}catch(e){let h=0;for(const c of String(text).trim())h=(h*31+c.charCodeAt(0))>>>0;return 'h'+h.toString(16)}
}
function qaTurns(events){
 let turns=[];
 for(const e of events||[]){
  if(!['intent','conops'].includes(e.phase_key))continue;
  let p=e.payload||{};
  if(e.kind==='waiting_input'||e.kind==='founder_decision'||p.waiting_input){
   let q=String(p.question||e.message||'').trim();
   if(q&&!(turns.length&&turns[turns.length-1].a===null&&turns[turns.length-1].q===q))turns.push({q,a:null});
  }else if(e.kind==='answer'){
   let m=String(e.message||''),a=String(p.answer||(m.startsWith('You: ')?m.slice(5):'')).trim();
   if(a){if(turns.length&&turns[turns.length-1].a===null)turns[turns.length-1].a=a}
  }
 }
 return turns.filter(t=>t.q&&t.a);
}

return {fbKey, qaActionsHtml, qaNotesHtml, questionNoteParts, qaChipsHtml, wireQaNotes, wireQaActions, contentRef, qaTurns, setFeedbackState, setClassificationState, setQaContext};
}
