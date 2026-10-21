/* The persistent shell: banner, header (brand, nav, cost, balance, identity) and footer.
   Mounted once at boot and never remounted by navigation; only its state re-renders.
   The DOM keeps the ids the rest of the app and the tests use: #sessioncost, #accountbalance,
   #identity, .preview-banner, header nav a.nav-link, footer. */
import {render,html,useState,useEffect} from './spa-vendor.js';
import {OPERATOR_PAGES,previewBannerText} from './chrome.js';

const PUBLIC_LINKS=[{href:'/',label:'Home'},{href:'/credits',label:'Add Credits'},{href:'/about',label:'About'}];

function isCurrent(href,path,hash){
  if(href.startsWith('/ops'))return path==='/ops'&&(href.includes('#')?href.split('#')[1]===(hash.slice(1)||'health'):true);
  if(href==='/')return path==='/';
  return path===href||path.startsWith(href+'/');
}

function Shell({initial}){
  const [state,setState]=useState({auth:null,path:location.pathname,hash:location.hash,menuOpen:false,runtime:null});
  useEffect(()=>{window.__shellSetState=(patch)=>setState(s=>({...s,...patch}));return ()=>{delete window.__shellSetState}},[]);
  const auth=state.auth,admin=!!(auth&&auth.authenticated&&auth.admin),banner=previewBannerText(initial);
  const opsLinks=OPERATOR_PAGES.map(p=>({href:'/ops#'+p.view,label:p.label,tip:p.tip,operator:true}));
  const links=[...PUBLIC_LINKS,...opsLinks];
  const link=(l)=>html`<a key=${l.href} class="nav-link" data-operator=${l.operator?l.operator:null} href=${l.href}
    title=${l.tip||null} aria-current=${isCurrent(l.href,state.path,state.hash)?'page':null}>${l.label}</a>`;
  return html`
    ${banner?html`<div class="preview-banner" role="status">${banner}</div>`:null}
    <header>
      <a class="brand" href="/"><span>R</span><div class="brand-name"><span>Requi</span><strong>.re</strong><small>Product definition workspace</small></div></a>
      <button id="navmenu" class="nav-menu" aria-label="Menu" aria-expanded=${state.menuOpen?'true':'false'}
        onClick=${()=>setState(s=>({...s,menuOpen:!s.menuOpen}))}>☰</button>
      <nav class=${state.menuOpen?'open':''} data-admin=${admin?'1':null} onClick=${()=>setState(s=>({...s,menuOpen:false}))}>
        ${PUBLIC_LINKS.map(link)}
        <span class="nav-ops" hidden=${!admin}>${opsLinks.map(link)}</span>
      </nav>
      <span id="sessioncost" class="session-cost" role="status" aria-label="Rolling Session Cost"></span>
      <span id="accountbalance" class="session-cost balance" role="status" aria-label="Credit Balance" hidden></span>
      <div id="identity" class="system" aria-label="Checking identity"><i></i></div>
    </header>
    <footer class="site-footer"><span>Requi.re — product definition workspace</span><span id="runtimeid">${state.runtime?('Build '+String(state.runtime).slice(0,7)):''}</span></footer>`;
}

/* The inline nav keeps its place while it physically fits. When it cannot (measured, not
   guessed), the whole link set moves into the menu as one decision; the reserved slots never
   move. Re-checked on resize, on sign-in (operator links appear) and once fonts settle. */
function updateNavFit(){
  const header=document.querySelector('header'),nav=header&&header.querySelector('nav');
  if(!header||!nav)return;
  if(header.classList.contains('nav-compact'))header.classList.remove('nav-compact');
  const needs=nav.scrollWidth>nav.clientWidth+1||header.scrollWidth>header.clientWidth+1;
  header.classList.toggle('nav-compact',needs);
}
let fitWired=false;
function wireNavFit(){
  if(fitWired)return;fitWired=true;
  addEventListener('resize',updateNavFit);
  if(document.fonts&&document.fonts.ready)document.fonts.ready.then(()=>updateNavFit());
}

export function mountShell(config){
  const top=document.querySelector('#shell-top'),bottom=document.querySelector('#shell-bottom');
  render(null,top);render(null,bottom);
  // One Preact tree owns the whole shell so header and footer share state; the outlet (main#app)
  // sits between them in the static HTML and is not part of the tree.
  const host=document.createElement('div');host.id='shell-tree';
  top.append(host);
  render(html`<${Shell} initial=${config}/>`,host);
  // Move footer after the outlet: the tree renders banner+header+footer together; keep DOM order banner,header,#app,footer.
  const footer=host.querySelector('footer');
  if(footer)bottom.append(footer);
  wireNavFit();requestAnimationFrame(updateNavFit);
  return {
    setAuth(auth){window.__shellSetState&&window.__shellSetState({auth});requestAnimationFrame(updateNavFit)},
    setRuntime(id){window.__shellSetState&&window.__shellSetState({runtime:id})},
    setLocation(path,hash){window.__shellSetState&&window.__shellSetState({path,hash})},
  };
}
