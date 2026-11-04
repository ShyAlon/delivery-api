import {mountOperationStatus} from '/assets/operation-status.js';
import {renderAuthScreen,mountAccountMenu} from '/assets/account-component.js';
import {api,setSessionExpiry,renewSessionIfDue} from '/assets/client-core.js';
import {Page} from '/assets/page-component.js';
Page.mountFrame({headerClass:'operator-header'});
const main=document.querySelector('main'),identity=document.querySelector('#identity');
const workspace=document.createElement('div');workspace.id='backoffice-workspace';workspace.append(...main.childNodes);main.append(workspace);
const authHost=document.createElement('div');workspace.before(authHost);let lastState={mode:'supabase',authenticated:false};
async function check(){
 workspace.hidden=true;main.classList.add('account-page');
 try{
  const state=await api('/api/auth');lastState=state;setSessionExpiry(state.expires_at);
  if(!state.authenticated){identity.replaceChildren();renderAuthScreen(authHost,state);return;}
  authHost.replaceChildren();workspace.hidden=false;main.classList.remove('account-page');
  mountAccountMenu(identity,state,{onDebug:()=>{location.hash='debug';document.querySelector('#debug-refresh')?.click()},onLogout:async()=>{await api('/auth/logout',{method:'POST',body:'{}'});location.reload()}});
  await import('./backoffice.js');
 }catch(error){authHost.textContent='Sign-in state unavailable. Reload to check again. '+error.message;}
}
window.addEventListener('requi:signin-needed',()=>{main.classList.add('account-page');workspace.hidden=true;document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());identity.replaceChildren();renderAuthScreen(authHost,{...lastState,authenticated:false})});
setInterval(()=>{if(!document.hidden)renewSessionIfDue().catch(()=>{})},60000);
const footer=Page.footer(document.body,{classes:'operator-bottom-bar'});mountOperationStatus(footer);
check();
