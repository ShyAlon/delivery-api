import {api,esc} from './client-core.js';
import {humanizeSnakeCase} from './format.js';

const LABELS={intent:'Start',conops:'Concept of operations',needs:'Stakeholder needs',srs:'Requirements',architecture:'Architecture',specs:'Item specifications',tests:'Verification',validation:'Validation'};
function itemTitle(item,index){return item.requirement_id||item.need_id||item.decision_id||item.spec_id||item.test_id||item.id||item.name||item.title||item.statement||`Result ${index+1}`}
function itemDetail(item){return item.statement||item.title||item.responsibility||item.description||item.summary||item.decision||item.value||item.body_markdown||''}
export function phaseOutputHtml(output){
  if(!output)return '<section class="phase-output" aria-live="polite"><h3>Phase results</h3><p>Loading results…</p></section>';
  const phase=LABELS[output.phase_key]||output.phase_key||'Phase';
  const shown=LABELS[output.display_phase]||output.display_phase||phase;
  const previous=output.status==='previous_phase';
  const title=previous?`${shown} results (previous phase)`:`${phase} outputs`;
  const note=previous?`${phase} is generating. These are completed ${shown} results, not ${phase} outputs.`:
    output.status==='local_preview'?`Phase result files are not part of a local preview. They stay on the server; this preview only shows what the database holds.`:
    output.status==='waiting'?`Waiting for the first persisted ${phase} output.`:
    output.status==='unavailable'?`A ${phase} output exists but could not be read.`:
    `${output.items.length} persisted result${output.items.length===1?'':'s'} from ${output.source||phase}.`;
  return `<section class="phase-output" aria-live="polite" data-display-phase="${esc(output.display_phase||output.phase_key||'')}" data-status="${esc(output.status)}"><h3>${esc(title)}</h3><p class="phase-output-note">${esc(note)}</p>${(output.items||[]).map((item,index)=>`<article class="phase-output-item"><b>${esc(humanizeSnakeCase(itemTitle(item,index)))}</b><p>${esc(String(itemDetail(item)).slice(0,420))}</p></article>`).join('')}</section>`;
}
export async function loadPhaseOutput(taskId,expectedPhase,generation,container){
  const result=await api(`/api/tasks/${encodeURIComponent(taskId)}/phase-output`);
  if(result.phase_key!==expectedPhase||result.generation!==generation)return false;
  if(container?.isConnected)container.innerHTML=phaseOutputHtml(result);
  return result.status||true;
}
