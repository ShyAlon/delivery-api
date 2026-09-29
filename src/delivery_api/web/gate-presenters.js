import {esc} from './client-core.js';
import {humanLabel} from './format.js';
export function gateStructured(value,depth=0){
 if(Array.isArray(value))return `<ol>${value.map(item=>`<li>${gateStructured(item,depth+1)}</li>`).join('')}</ol>`;
 if(value&&typeof value==='object')return Object.entries(value).map(([key,item])=>`<section class="gate-structured-field"><b>${esc(humanLabel(key))}</b>${gateStructured(item,depth+1)}</section>`).join('');
 return `<p>${esc(value==null?'':String(value))}</p>`;
}
export function gateSummary(value){const result=[];function walk(item){if(result.length>=5)return;if(Array.isArray(item)){item.forEach(walk);return}if(item&&typeof item==='object'){for(const [key,part] of Object.entries(item)){if(result.length>=5)break;if(['title','statement','description','summary','problem_statement','decision','rationale','name'].includes(key)&&typeof part==='string')result.push(part);else if(typeof part==='object')walk(part)}}}walk(value);return result.join(' · ').slice(0,750)}
