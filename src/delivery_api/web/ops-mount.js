/* Mounts the operator views (ops.js) into the outlet, inside the persistent shell. */
import {mountOpsPage} from './ops.js';

let template=null;
export async function mountOps(ctx,hash){
  const app=document.querySelector('#app');
  if(!template)template=await (await fetch('/assets/ops-body.html',{cache:'no-cache'})).text();
  app.innerHTML=template;
  const view=(hash||'').slice(1);
  mountOpsPage(app,['health','stops','spend','grant'].includes(view)?view:null);
}
