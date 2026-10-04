import {esc} from './client-core.js';
import {humanLabel,markdownHtml} from './format.js';
/* A spec-style item carries its document as body_markdown. Show it as a collapsible panel (closed) titled by the document's own first heading, with the body rendered as markdown. */
const specTitle=(item,body)=>{const h=body.match(/^#{1,2} (.+)$/m);return (h?h[1]:(item.title||item.name||item.spec_id||'Item')).trim()};
const isDocItem=v=>v&&typeof v==='object'&&!Array.isArray(v)&&typeof v.body_markdown==='string'&&v.body_markdown.trim();
function docPanel(item,depth,opts){const body=item.body_markdown,title=specTitle(item,body),rest={...item};delete rest.body_markdown;if(rest.spec_id&&title.includes(rest.spec_id))delete rest.spec_id;return `<details class="context-box spec-item"><summary>${esc(title)}</summary><div class="context-body">${gateStructured(rest,depth+1,opts)}<article class="artifact-readable spec-body">${markdownHtml(body.replace(/^#{1,2} .+\n?/m,''))}</article></div></details>`}
export function gateStructured(value,depth=0,opts={}){
 const {hide=null,label=humanLabel}=opts;
 if(value===null||value===undefined)return opts.skipEmpty?'':`<p></p>`;
 if(Array.isArray(value)&&value.length&&value.every(isDocItem))return `<div class="spec-items">${value.map(item=>docPanel(item,depth,opts)).join('')}</div>`;
 if(Array.isArray(value))return `<ol>${value.map(item=>`<li>${gateStructured(item,depth+1,opts)}</li>`).join('')}</ol>`;
 if(value&&typeof value==='object')return Object.entries(value).filter(([key])=>!(hide&&hide.has(key))).map(([key,item])=>`<section class="gate-structured-field"><b>${esc(label(key))}</b>${gateStructured(item,depth+1,opts)}</section>`).join('');
 return `<p>${esc(String(value))}</p>`;
}
export function gateSummary(value){const result=[];function walk(item){if(result.length>=5)return;if(Array.isArray(item)){item.forEach(walk);return}if(item&&typeof item==='object'){for(const [key,part] of Object.entries(item)){if(result.length>=5)break;if(['title','statement','description','summary','problem_statement','decision','rationale','name'].includes(key)&&typeof part==='string')result.push(part);else if(typeof part==='object')walk(part)}}}walk(value);return result.join(' · ').slice(0,750)}
/* Founder views never show record IDs. The stored value is untouched; only the displayed copy drops them. */
export const stripIds=v=>Array.isArray(v)?v.map(stripIds):v&&typeof v==='object'?Object.fromEntries(Object.entries(v).filter(([k])=>!/(^|_)(id|ids|ref|refs)$/.test(k)).map(([k,x])=>[k,stripIds(x)])):v;
