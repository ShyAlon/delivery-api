import {esc} from './client-core.js';
import {humanLabel} from './format.js';

/* Presentation helpers for the "Review flagged inputs" page. Nothing here changes what is
   sent to the server: decisions stay {item_id, action, replacement?, rationale}. */

const SECTION={constraints:'Constraint',assumptions:'Assumption',functional:'Functional requirement',non_functional:'Non-functional requirement',
 stakeholder_needs:'Stakeholder need',needs:'Stakeholder need',scenarios:'Scenario',risks:'Risk',glossary:'Glossary term'};
const TEXT_KEYS=['statement','title','name','description','text','summary','requirement','rationale','label'];
const ID_KEYS=['requirement_id','need_id','constraint_id','id'];
const words=(s,n)=>{const w=String(s).trim().split(/\s+/);return w.length>n?w.slice(0,n).join(' ')+'…':w.join(' ')};
/* Short form for a title: the clause before the first colon ("Deployment is mandated: build with..."
   -> "Deployment is mandated"). With no colon, the first six words; the full text is shown below the title. */
const shortText=t=>{const s=String(t).trim(),i=s.indexOf(':');return i>0?s.slice(0,i).trim():words(s,6)};

export function findingText(original){
 if(typeof original==='string')return original;
 if(original&&typeof original==='object'&&!Array.isArray(original)){
  for(const k of TEXT_KEYS)if(typeof original[k]==='string'&&original[k].trim())return original[k];
  const first=Object.values(original).find(v=>typeof v==='string'&&v.trim());if(first)return first}
 return '';
}
/* A title a founder can recognise: what kind of item, its own id when it has one, and its wording. */
export function findingTitle(f){
 const path=f.path||[],head=path.find(p=>typeof p==='string'),index=path.find(p=>typeof p==='number');
 const kind=SECTION[head]||(head?humanLabel(head):'Item');
 const o=f.original,id=o&&typeof o==='object'?ID_KEYS.map(k=>o[k]).find(v=>typeof v==='string'&&v):'';
 const text=findingText(o);
 return `${kind}${id?' '+id:''}${text?': '+shortText(text):(index!==undefined?` (number ${index+1})`:'')}`;
}

export const OPTIONS=[
 {value:'accept',label:'Keep as written',tip:'Keep this exactly as written, even though it was flagged. You are overriding the flag.'},
 {value:'remove',label:'Remove',tip:'Delete this item. Nothing is added in its place.'},
 {value:'replace',label:'Replace',tip:'Rewrite this item yourself. The fields to edit appear below.'}];
const STRUCT_TIP='Not available: this item has a structural problem, so it cannot be kept as written. Remove it or replace it.';

export function pillsHtml(i,title,structural){
 return `<fieldset class="decision-pills" role="radiogroup" aria-label="Decision for ${esc(title)}"><legend>What should happen to this item?</legend>${OPTIONS.map(o=>{
  const off=o.value==='accept'&&structural,tip=off?STRUCT_TIP:o.tip;
  return `<label class="pill${off?' disabled':''}" title="${esc(tip)}"><input type="radio" name="action-${i}" value="${o.value}" required ${off?'disabled':''} aria-describedby="tip-${i}-${o.value}"><span>${esc(o.label)}</span><small id="tip-${i}-${o.value}" class="pill-tip">${esc(tip)}</small></label>`}).join('')}</fieldset><p class="pill-caption" id="caption-${i}" aria-live="polite">Choose one. Nothing is decided for you.</p>`;
}

const FIXED=/(^|_)(id|ids|ref|refs)$/;
const input=(path,type,value,key,extra='')=>`data-path='${esc(JSON.stringify(path))}' data-type="${type}" ${extra}`;
function fieldHtml(path,value,key,fixed){
 const label=humanLabel(String(key??'Value')),lock=fixed||FIXED.test(String(key));
 const attr=(type,extra='')=>`data-path='${esc(JSON.stringify(path))}' data-type="${type}" ${lock?'readonly':''} ${extra}`;
 if(Array.isArray(value)){
  if(value.every(v=>v===null||typeof v!=='object'))return `<label class="rf"><span>${esc(label)} <small>one per line${lock?' · fixed':''}</small></span><textarea rows="${Math.min(6,Math.max(2,value.length))}" ${attr('list')} data-num="${value.length&&value.every(v=>typeof v==='number')?1:0}">${esc(value.join('\n'))}</textarea></label>`;
  return `<fieldset class="rf-group"><legend>${esc(label)}</legend>${value.map((v,n)=>fieldHtml([...path,n],v,`${label} ${n+1}`,lock)).join('')}</fieldset>`}
 if(value&&typeof value==='object')return `<fieldset class="rf-group"><legend>${esc(label)}</legend>${Object.entries(value).map(([k,v])=>fieldHtml([...path,k],v,k,lock)).join('')}</fieldset>`;
 if(typeof value==='boolean')return `<label class="rf rf-check"><input type="checkbox" ${attr('bool')} ${lock?'disabled':''} ${value?'checked':''}><span>${esc(label)}</span></label>`;
 if(typeof value==='number')return `<label class="rf"><span>${esc(label)}${lock?' <small>fixed</small>':''}</span><input type="number" step="any" value="${esc(value)}" ${attr('number')}></label>`;
 const text=value===null||value===undefined?'':String(value),big=text.length>90||text.includes('\n');
 return `<label class="rf"><span>${esc(label)}${lock?' <small>fixed · IDs cannot change</small>':''}</span>${big?`<textarea rows="3" ${attr(value===null?'nullable':'text')}>${esc(text)}</textarea>`:`<input type="text" value="${esc(text)}" ${attr(value===null?'nullable':'text')}>`}</label>`;
}
/* Typed inputs that mirror the flagged item's own shape, prefilled with its current values. */
export function replaceFieldsHtml(i,original){
 return `<div class="replace-fields hidden" data-for="${i}" aria-label="Replacement values">${fieldHtml([],original,original&&typeof original==='object'?'':'Replacement',false)}</div>`;
}
const setPath=(root,path,value)=>{if(!path.length)return value;let o=root;path.slice(0,-1).forEach(p=>{o=o[p]});o[path[path.length-1]]=value;return root};
export function readReplacement(form,i,original){
 let result=JSON.parse(JSON.stringify(original??null));
 for(const el of form.querySelectorAll(`.replace-fields[data-for="${i}"] [data-path]`)){
  const path=JSON.parse(el.dataset.path),type=el.dataset.type;let v;
  if(type==='bool')v=el.checked;
  else if(type==='number'){if(el.value.trim()===''||Number.isNaN(Number(el.value)))throw new Error(`${humanLabel(String(path[path.length-1]??'Value'))} must be a number.`);v=Number(el.value)}
  else if(type==='list'){v=el.value.split('\n').map(s=>s.trim()).filter(Boolean);if(el.dataset.num==='1'){v=v.map(Number);if(v.some(Number.isNaN))throw new Error('Every line must be a number.')}}
  else if(type==='nullable')v=el.value===''?null:el.value;
  else v=el.value;
  result=setPath(result,path,v)}
 return result;
}
/* Restore saved values (a draft or an earlier decision) into the typed inputs. */
export function fillReplacement(form,i,saved){
 for(const el of form.querySelectorAll(`.replace-fields[data-for="${i}"] [data-path]`)){
  let v=saved;try{for(const p of JSON.parse(el.dataset.path))v=v?.[p]}catch{v=undefined}
  if(v===undefined)continue;
  if(el.dataset.type==='bool')el.checked=Boolean(v);else if(el.dataset.type==='list'&&Array.isArray(v))el.value=v.join('\n');else el.value=v===null?'':String(v)}
}
