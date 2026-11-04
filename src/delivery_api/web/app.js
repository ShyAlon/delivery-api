import {mountOperationStatus} from './operation-status.js';
/* Boot: mount the persistent shell once, load identity into it, then run the router.
   Pages render into the persistent app outlet exactly as before; navigation only
   decides which page function runs and owns cleanup (timers, streams, listeners). */
import {createPurchase} from './purchase.js';
import {createDecisions} from './decisions.js';
import {createQa} from './qa.js';
import {createIdentity} from './identity.js';
import {createDialogs} from './dialogs.js';
import {createDashboard} from './dashboard.js';
import {createTask} from './task.js';
import {createGate} from './gate.js';
import {createCompletion} from './completion.js';
import {createTaskActions} from './taskactions.js';
import {clientLog,esc} from './client-core.js';
import {startCopyBlocks} from './copy-blocks.js';
import {mountShell} from './spa-shell.js';
import {defineRoutes,startRouter,navigate,onRouteChange} from './spa-router.js';
import {onPageExit} from './page-lifecycle.js';
import {mountOps} from './ops-mount.js';
import {startSessionCostHeader} from './session-cost.js';

window.addEventListener('click',e=>{let target=e.target.closest('a,button,[role="button"]');if(target)clientLog('click',target.getAttribute('aria-label')||target.textContent.trim().replace(/\s+/g,' ').slice(0,120),{tag:target.tagName.toLowerCase()})},true);
window.addEventListener('error',e=>ctx.debugState.client_errors.push({ts:new Date().toISOString(),message:e.message,source:e.filename,line:e.lineno}));window.addEventListener('unhandledrejection',e=>ctx.debugState.client_errors.push({ts:new Date().toISOString(),message:String(e.reason&&e.reason.message||e.reason),source:'unhandledrejection'}));
clientLog('navigation','Page loaded',{url:location.href});

// The fonts are self-hosted; if one cannot be fetched or parsed the shell keeps its geometry
// (font-display:swap, fixed slots) and the failure is recorded here, not silently absorbed.
if(document.fonts&&document.fonts.ready)document.fonts.ready.then(()=>{
  Promise.all(['DM Mono','Manrope'].map(f=>document.fonts.load('12px "'+f+'"').then(r=>({f,ok:r.length>0})).catch(()=>({f,ok:false})))).then(rs=>{
    const missing=rs.filter(r=>!r.ok).map(r=>r.f);
    if(missing.length)clientLog('fonts.unavailable','Self-hosted font failed to load',{families:missing});
  });
});
startCopyBlocks();

const config=window.__REQUI_CONFIG__||{};
const shell=mountShell(config);mountOperationStatus(document.querySelector('footer'));
const ctx={};
for(const create of [createPurchase,createQa,createIdentity,createDialogs,createDashboard,createTask,createGate,createCompletion,createDecisions,createTaskActions])Object.assign(ctx,create(ctx));
const {purchasePage,loadIdentity,decisionPage,gatePage,completePage,aboutPage,detail,dashboard,showError}=ctx;
const mountError=e=>{if(e&&e.name==='AbortError')return;showError(e)};   // leaving a page cancels its loads; that is navigation, not an error
window.addEventListener('unhandledrejection',e=>showError(e.reason));

fetch('/health',{cache:'no-store'}).then(r=>r.json()).then(live=>shell.setRuntime(live.runtime_id)).catch(()=>{});

let stopCostHeader=null;
onRouteChange((path,hash)=>{
  shell.setLocation(path,hash);
  if(stopCostHeader){try{stopCostHeader()}catch{}stopCostHeader=null}
  if(authOk)stopCostHeader=startSessionCostHeader();
});

let authOk=false;
defineRoutes([
  {path:'/',mount:()=>dashboard().catch(mountError)},
  {path:'/credits',mount:()=>purchasePage().catch(mountError)},
  {path:'/about',mount:()=>{aboutPage()}},
  {path:'/tasks/{taskId}/view',mount:p=>detail(p.taskId).catch(mountError)},
  {path:'/tasks/{taskId}/decisions/{waitId}',mount:p=>decisionPage(p.taskId,p.waitId).catch(mountError)},
  {path:'/tasks/{taskId}/gates/{gateKey}',mount:p=>gatePage(p.taskId,p.gateKey).catch(mountError)},
  {path:'/tasks/{taskId}/complete',mount:p=>completePage(p.taskId).catch(mountError)},
  {path:'/ops',mount:(p,hash)=>mountOps(ctx,hash).catch(mountError)},
],(path)=>{
  const app=document.querySelector('#app');
  app.innerHTML=`<section class="empty-card"><div><strong>Page not found</strong><p>${esc(path)} does not exist. Use the navigation above.</p></div></section>`;
});

loadIdentity().then(ok=>{
  if(!ok)return;
  authOk=true;
  shell.setAuth(ctx.getAuthState());
  stopCostHeader=startSessionCostHeader();
  startRouter();
}).catch(showError);
