/* Client-side router: navigation swaps only the outlet content (main#app); the shell persists. */
import {leavePage,onPageExit,pageEpoch} from './page-lifecycle.js';
import {clientLog} from './client-core.js';

const routes=[]; // {pattern:RegExp, keys:[], mount:(params)=>Promise<void>|void}
function route(pattern,mount){
  const keys=[];const rx=new RegExp('^'+pattern.replace(/[.*+?^${}()|[\]\\]/g,'\\$&').replace(/\\\{(\w+)\\\}/g,(_,k)=>{keys.push(k);return '([^/]+)'})+'$');
  routes.push({rx,keys,mount,pattern});
}
let current=null; // {path, epoch}
let notFound=()=>{};
let onNavigated=()=>{};

export function onRouteChange(fn){onNavigated=fn}
export function defineRoutes(defs,notFoundMount){
  for(const d of defs)route(d.path,d.mount);
  notFound=notFoundMount;
}
export function currentPath(){return location.pathname}
export function navigate(path,{replace=false}={}){
  if(path===location.pathname+location.hash)return;
  history[replace?'replaceState':'pushState']({},'',path);
  dispatch();
}
export async function dispatch(){
  const path=location.pathname,hash=location.hash;
  leavePage();
  const app=document.querySelector('#app');
  if(app){app.classList.remove('view-in');app.innerHTML='<div class="loading route-loading" role="status">Loading\u2026</div>'}
  const my=pageEpoch();
  current={path,epoch:my};
  for(const r of routes){
    const m=path.match(r.rx);
    if(!m)continue;
    const params={};r.keys.forEach((k,i)=>params[k]=decodeURIComponent(m[i+1]));
    clientLog('navigation','Client route',{url:path+hash});
    await r.mount(params,hash);
    if(pageEpoch()!==my)return; // navigated away while mounting
    if(app&&!app.querySelector('.task-error-state,[role="alert"],.gate-evidence-error,.route-loading'))app.classList.add('view-in');   // errors render instantly, never faded
    onNavigated(path,hash);
    return;
  }
  clientLog('navigation','No client route matched',{url:path+hash});
  await notFound(path);
  if(pageEpoch()!==my)return;
  if(app&&!app.querySelector('.task-error-state,[role="alert"],.gate-evidence-error,.route-loading'))app.classList.add('view-in');
  onNavigated(path,hash);
}
export function startRouter(){
  window.addEventListener('popstate',()=>{clientLog('navigation','Browser history navigation',{url:location.href});dispatch()});
  document.addEventListener('click',e=>{
    if(e.defaultPrevented||e.button!==0||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;
    const a=e.target.closest('a[href]');
    if(!a)return;
    const href=a.getAttribute('href');
    if(!href||!href.startsWith('/')||href.startsWith('//'))return;
    if(a.target||a.hasAttribute('download'))return;
    e.preventDefault();
    navigate(href);
  });
  dispatch();
}
