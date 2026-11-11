/* Shared activation boundary: a text selection is not a click action.
   Keep only gesture metadata, never selected text, input values or coordinates. */
import {clientLog} from './client-core.js';
let press=null;
const name=node=>node?.tagName?.toLowerCase()||'none';
const selected=()=>{const field=document.activeElement;return !!window.getSelection()?.toString()||!!(field&&typeof field.selectionStart==='number'&&field.selectionStart!==field.selectionEnd)};
document.addEventListener('pointerdown',e=>{press={target:e.target};},true);
export function activationAllowed(event,{backdrop=false}={}){
 if(event.type==='keydown')return true;
 const selection=selected(),crossed=backdrop&&press&&press.target!==event.target;
 if(!selection&&!crossed)return true;
 clientLog('ui.activation','Selection gesture ignored',{action:backdrop?'backdrop':'navigation',selection,crossed:Boolean(crossed),origin:name(press?.target),target:name(event.target)});
 event.preventDefault();event.stopPropagation();return false;
}
export function navigationCall(source){
 const frames=(new Error().stack||'').split('\n').slice(2,6).map(line=>line.replace(/https?:\/\/[^/\s]+/g,'').split('?')[0].trim());
 clientLog('navigation.caller','Navigation requested',{source,frames});
}
// Focus changes can distinguish a dismissed dialog from a route transition.
for(const kind of ['focusin','focusout'])document.addEventListener(kind,e=>{if(e.target.closest?.('[role=dialog]'))clientLog('ui.focus',kind,{target:name(e.target),selection:selected()})},true);

// Capture all same-document URL changes, including code outside the router.
// Preserve native receiver/arguments/return and never record state or URL query.
for(const method of ['pushState','replaceState']){
 const original=history[method];
 history[method]=function(...args){const result=original.apply(this,args);navigationCall('history.'+method);return result};
}
