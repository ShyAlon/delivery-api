import {renderAuthScreen,mountAccountMenu} from '/assets/account-component.js';
import {api,setSessionExpiry,renewSessionIfDue} from '/assets/client-core.js';
import {Page} from '/assets/page-component.js';
Page.mountFrame({headerClass:'operator-header'});
const workspace=document.querySelector('main'),identity=document.querySelector('#identity');
const authHost=document.createElement('div');workspace.before(authHost);let lastState={mode:'supabase',authenticated:false};
async function check(){
 workspace.hidden=true;
 try{
  const state=await api('/api/auth');lastState=state;setSessionExpiry(state.expires_at);
  if(!state.authenticated){identity.replaceChildren();renderAuthScreen(authHost,state);return;}
  authHost.replaceChildren();workspace.hidden=false;
  mountAccountMenu(identity,state,{onDebug:()=>{location.hash='debug';document.querySelector('#debug-refresh')?.click()},onLogout:async()=>{await api('/auth/logout',{method:'POST',body:'{}'});location.reload()}});
  await import('./backoffice.js');
 }catch(error){authHost.textContent='Sign-in state unavailable. Reload to check again. '+error.message;}
}
window.addEventListener('requi:signin-needed',()=>{workspace.hidden=true;document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());identity.replaceChildren();renderAuthScreen(authHost,{...lastState,authenticated:false})});
setInterval(()=>{if(!document.hidden)renewSessionIfDue().catch(()=>{})},60000);
check();
