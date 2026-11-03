import {Dialog} from './shared-dialog.js';
import {esc} from './client-core.js';
import {humanLabel} from './format.js';

// One plain sentence per phase: what the engine is doing while the founder waits.
// Phase keys, order and artifacts come from phases.py (intent, conops, needs, srs,
// architecture, specs, tests); the wording only describes them.
export const PHASE_EXPLAIN={
 intent:'Reading your idea and preparing the first interview questions.',
 conops:'Writing the concept of operations: how the product works day to day, from the answers you gave.',
 needs:'Organizing your answers into stakeholder needs.',
 srs:'Turning the stakeholder needs into numbered requirements.',
 architecture:'Drafting the architecture that satisfies those requirements.',
 specs:'Writing a specification for each item in the architecture.',
 tests:'Defining how each requirement will be verified.'
};
const phaseLabel=(t,key)=>t.phases?.find(p=>p.phase_key===key)?.label||humanLabel(key);

/* Decide whether to show the phase-change dialog. `transition` is the saved gate decision
   still waiting for its target phase to start; `prev` is the active phase at the last render
   of this task. Returns null when nothing is changing or the founder can act. */
export function phaseChange(t,view,transition,prev){
 const phases=t.phases||[],keys=phases.map(p=>p.phase_key);
 let to=null,from=null;
 if(transition?.target){to=transition.target;from=keys[keys.indexOf(to)-1]||null}
 else if(prev&&t.active_phase&&prev!==t.active_phase&&view.mode==='executing'){to=t.active_phase;from=prev}
 if(!to||!keys.includes(to))return null;
 const phase=phases.find(p=>p.phase_key===to),events=t.events||[];
 const last=[...events].reverse().find(e=>e.kind==='progress'&&e.phase_key===to);
 return {to,from,toLabel:phaseLabel(t,to),fromLabel:from?phaseLabel(t,from):null,position:keys.indexOf(to)+1,total:keys.length,
  explain:PHASE_EXPLAIN[to]||`Preparing ${phaseLabel(t,to)}.`,latest:last?.message||'',
  count:phase?.total?`${Number(phase.completed||0)} of ${Number(phase.total)} done`:''};
}

export function phaseChangeHtml(info){
 const flow=info.fromLabel?`<div class="phase-flow" aria-hidden="true"><b>${esc(info.fromLabel)}</b><i>→</i><b class="to">${esc(info.toLabel)}</b></div>`:'';
 return Dialog.html(`<button class="dialog-x" aria-label="Close phase change">×</button>
<div class="eyebrow">PHASE ${info.position} OF ${info.total} · INPUT PAUSED</div>
<h2>${info.fromLabel?`Moving from ${esc(info.fromLabel)} to ${esc(info.toLabel)}`:`Starting ${esc(info.toLabel)}`}</h2>${flow}
<p class="phase-explain">${esc(info.explain)}</p>
<p class="phase-live"><span class="waiting-spinner" aria-hidden="true"></span><span id="transitionprogress">${esc(info.latest?'Latest: '+info.latest:'Starting up…')}</span>${info.count?` <small>${esc(info.count)}</small>`:''}</p>
<p class="phase-pause">Nothing for you to do right now. Your input is paused while this runs, and this closes by itself when the next step is ready for you.</p>
<div class="dialog-actions"><button class="secondary" id="transitionhide">Keep watching in the background</button></div>`,{"tag":"div","attributes":{"class":"dialog phase-change-dialog","aria-label":"Phase change","aria-live":"polite"}});
}
