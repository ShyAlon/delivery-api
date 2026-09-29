import {esc} from './client-core.js';
export function vmap(phases,gates=[],taskId=''){
 const pts=[[22,20],[70,65],[118,110],[166,154],[214,110],[262,65],[310,20]];
 const done=phases.filter(p=>['approved','done'].includes(p.state)).length;
 let path='M'+pts.map(p=>p.join(',')).join(' L'); let passed=pts.slice(0,Math.max(1,done+1));
 const gateMarks=gates.map(g=>{let i=phases.findIndex(p=>p.phase_key===g.after_phase);if(i<0)return '';let a=pts[i],b=pts[i+1]||[a[0]+24,a[1]],x=(a[0]+b[0])/2,y=(a[1]+b[1])/2,cls=g.state==='waiting'?'waiting':(g.is_open?'open':'closed');return `<g class="gate ${cls}" data-task="${esc(taskId)}" data-gate="${esc(g.gate_key)}" tabindex="0"><rect x="${x-6}" y="${y-6}" width="12" height="12" rx="3" transform="rotate(45 ${x} ${y})"/><title>${esc(g.label)} · ${cls}</title></g>`}).join('');
 return `<div class="v-map"><svg viewBox="0 0 332 175"><path class="rail" d="${path}"/><path class="passed" d="M${passed.map(p=>p.join(',')).join(' L')}"/>${phases.map((p,i)=>`<circle class="${p.state}" cx="${pts[i][0]}" cy="${pts[i][1]}" r="7"/><text x="${pts[i][0]}" y="${pts[i][1]+(i===3?23:(i<3?-13:22))}" text-anchor="middle">${esc(p.label.split(' ')[0])}</text>`).join('')}${gateMarks}</svg></div>`;
}

export function stateLabel(s){ return s==='not_started'?'Not started':s==='running'?'In progress':s==='done'?'Complete':s==='waiting_gate'?'Waiting at gate':'Needs attention'; }
