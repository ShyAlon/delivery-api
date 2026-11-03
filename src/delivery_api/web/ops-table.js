import {Table} from './table-component.js';
// The operator tables: a filter box, sortable headers, paging, and a scrolling body only. Used by ops.js and tested on its own.
const el=(tag,props,...kids)=>{const n=document.createElement(tag);Object.assign(n,props||{});kids.forEach(k=>n.append(k));return n};
export {el};
export const makeTable=(...args)=>Table.interactive(...args);

export const tableCell=(...args)=>Table.cell(...args);
export const tableRow=(...args)=>Table.row(...args);
export function detailTable(host,labels,items,values){const slot=document.createElement('div');host.append(slot);return new Table(slot,{columns:labels}).set(items,values).node;}
export function fieldList(host,fields){const list=el('dl',{className:'detail-fields'});for(const [label,value] of fields)list.append(el('dt',{textContent:label}),el('dd',{textContent:String(value??'Unavailable')}));host.append(list);return list;}

export const mountSort=(...args)=>Table.sortControl(...args);
