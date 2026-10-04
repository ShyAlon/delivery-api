import {esc} from './client-core.js';
import {humanLabel} from './format.js';
export function gateStructured(value,depth=0,opts={}){
 const {hide=null,label=humanLabel}=opts;
 if(value===null||value===undefined)return opts.skipEmpty?'':`<p></p>`;
 if(Array.isArray(value))return `<ol>${value.map(item=>`<li>${gateStructured(item,depth+1,opts)}</li>`).join('')}</ol>`;
 if(value&&typeof value==='object')return Object.entries(value).filter(([key])=>!(hide&&hide.has(key))).map(([key,item])=>`<section class="gate-structured-field"><b>${esc(label(key))}</b>${gateStructured(item,depth+1,opts)}</section>`).join('');
 return `<p>${esc(String(value))}</p>`;
}
export function gateSummary(value){const result=[];function walk(item){if(result.length>=5)return;if(Array.isArray(item)){item.forEach(walk);return}if(item&&typeof item==='object'){for(const [key,part] of Object.entries(item)){if(result.length>=5)break;if(['title','statement','description','summary','problem_statement','decision','rationale','name'].includes(key)&&typeof part==='string')result.push(part);else if(typeof part==='object')walk(part)}}}walk(value);return result.join(' · ').slice(0,750)}
/* Founder views never show record IDs. The stored value is untouched; only the displayed copy drops them. */
export const stripIds=v=>Array.isArray(v)?v.map(stripIds):v&&typeof v==='object'?Object.fromEntries(Object.entries(v).filter(([k])=>!/(^|_)(id|ids|ref|refs)$/.test(k)).map(([k,x])=>[k,stripIds(x)])):v;
