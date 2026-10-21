/* Mounts the operator views (ops.js) into the outlet, inside the persistent shell. */
import {mountOpsPage} from './ops.js';
import {pageEpoch,pageStale,pageController} from './page-lifecycle.js';

let template=null;
export async function mountOps(ctx,hash){
  const at=pageEpoch();
  const app=document.querySelector('#app');
  if(!template)template=await (await fetch('/assets/ops-body.html',{cache:'no-cache',signal:pageController().signal})).text();
  if(pageStale(at))return;   // navigated away while the template loaded: never commit, never register cleanups
  app.innerHTML=template;
  const view=(hash||'').slice(1);
  mountOpsPage(app,['health','stops','spend','grant'].includes(view)?view:null);
}
