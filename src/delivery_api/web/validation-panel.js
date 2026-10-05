import {esc} from './client-core.js';

// Read-only view of the Validation plan: what a person does, grouped by the need it checks.
// Closed by default. No record ids; a need is shown by its statement.
const row=(label,value)=>value?`<div class="validation-row"><b>${esc(label)}</b><span>${esc(value)}</span></div>`:'';

function itemHtml(item){
  const parts=[
    row('What You Do',item.what_you_do),
    row('What To Look For',item.what_to_look_for),
    row('Pass When',item.pass_when),
    row('Where',item.where),
    row('Record',item.how_to_record_it),
    item.components?.length?row('Parts Involved',item.components.join(', ')):'',
    row('Automatic Checks',String(item.automatic_checks||0)),
  ].join('');
  return `<details class="validation-item"><summary>${esc(item.what_you_do||'Validation Item')}</summary>${parts}</details>`;
}

export function validationPanelHtml(panel){
  const groups=panel?.groups||[];
  if(!groups.length)return '<p class="validation-empty">No Validation Items</p>';
  return `<div class="validation-panel">${groups.map(g=>{
    const n=g.items.length;
    const badge=g.uncovered?'<span class="validation-flag">Not Covered</span>':`<span class="validation-count">${n} ${n===1?'Item':'Items'}</span>`;
    return `<details class="validation-need"><summary><span>${esc(g.need)}</span>${badge}</summary>${g.items.map(itemHtml).join('')}</details>`;
  }).join('')}</div>`;
}
