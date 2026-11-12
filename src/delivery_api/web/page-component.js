/* One Page owns landmarks, page-heading geometry, persistent shell and route lifecycle. */
let render,html,useState,useEffect;
import {OPERATOR_PAGES} from './chrome.js';
const pageStyles={"/assets/style.css": "header{height:84px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 4.5vw;background:rgba(242,240,233,.84);backdrop-filter:blur(16px);position:sticky;top:0;z-index:4}main{padding:54px 4.5vw 80px;width:100%;max-width:1500px;margin:auto}.detail-head{display:flex;justify-content:space-between;gap:20px;align-items:end;margin-bottom:34px}.detail-head h1{font-size:var(--fs-display);letter-spacing:-.045em;margin:11px 0 5px}@media (max-width:850px){header{padding:0 20px}main{padding:35px 20px}.detail-head h1{font-size:var(--fs-display)}}.page-head{position:sticky;top:96px;z-index:3;background:rgba(242,240,233,.91);backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,.75);box-shadow:0 12px 34px rgba(26,42,34,.08);padding:18px;border-radius:20px}@media (max-width:850px){.page-head{top:92px}}.detail-head.glass-head{padding:12px 16px;margin-bottom:22px;align-items:center}.detail-head.glass-head h1{font-size:var(--fs-xl);margin:5px 0 2px}.detail-head.glass-head .eyebrow{font-size:var(--fs-xs)}.detail-head.glass-head+.bundle-panel{padding-top:4px}.detail-head.page-head{background:rgb(242,240,233);isolation:isolate}@media (max-width:900px){.detail-head.glass-head{position:sticky;align-items:stretch}.detail-head.glass-head h1{font-size:var(--fs-xl)}}.detail-head.glass-head{padding:9px 14px}.detail-head.glass-head h1{font-size:var(--fs-xl)}.bundle-workspace>.page-head{top:84px;margin:0 0 22px;border-radius:24px 24px 18px 18px}@media (max-width:760px){.decision-page .page-head{position:static;top:auto;z-index:auto}}@media (max-width:700px){.detail-head.page-head{position:static}.detail-head{display:block}.detail-head>div+div{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}.detail-head h1{font-size:var(--fs-xl)}}@media (min-width:1101px){main:has(.detail-layout){padding-top:20px}.detail-head.page-head{padding:12px 18px;margin-bottom:14px}.detail-head.page-head h1{font-size:var(--fs-xl);margin:4px 0 2px}}.decision-page:not(.semantic-page) .page-head{position:static}.compact-head{display:grid;grid-template-columns:auto 1fr auto;gap:8px 24px;align-items:center;padding:12px 18px}.compact-head h1{font-size:var(--fs-xl);margin:2px 0;letter-spacing:-.02em}.compact-head p{margin:0;color:var(--muted);font-size:var(--fs-md)}.compact-head .ch-left{display:grid;gap:2px}.compact-head .ch-main{min-width:0}.compact-head #review-permalink{font:var(--fs-xs) DM Mono;white-space:nowrap}.decision-page:not(.semantic-page) .page-head{position:sticky;top:84px;background:rgb(242,240,233);z-index:5}@media (max-width:700px){.compact-head{grid-template-columns:1fr;gap:6px;padding:12px 14px}.compact-head h1{font-size:var(--fs-lg)}.compact-head #review-permalink,.compact-head #gate-permalink,.compact-head #semantic-link{white-space:normal}}.complete-page .page-head{position:relative;top:auto;margin-bottom:18px}@media (max-width:850px){header{flex-wrap:wrap;height:auto;min-height:84px;padding-top:10px;padding-bottom:10px;gap:8px}.semantic-page .semantic-head,.page-head{top:110px}}header{z-index:6}.page-head,.bundle-workspace>.page-head,.gate-page-head,.decision-page:not(.semantic-page) .page-head,.semantic-page .semantic-head{top:var(--global-header-height,84px)}.detail-head.page-head{position:relative;top:auto}", "/assets/chrome.css": "#shell-top,#shell-tree{display:contents}header nav{flex-wrap:wrap;row-gap:6px}header .session-cost{margin-left:0}header .session-cost:empty{display:none}header .avatar{padding:0;border:0;line-height:1;box-sizing:border-box;font:var(--fs-xs) DM Mono}header nav a.nav-link{padding:6px 2px;border-bottom:2px solid transparent}header nav a.nav-link:hover{border-bottom-color:var(--line)}header nav a.nav-link[aria-current=\"page\"]{border-bottom-color:var(--green);font-weight:500}.site-footer{display:flex;align-items:center;gap:18px;flex-wrap:wrap;padding:14px 34px;margin-top:40px;border-top:1px solid var(--line);font:var(--fs-xs) DM Mono;letter-spacing:.08em;color:var(--muted);flex-shrink:0}.site-footer .grow{flex:1}.site-footer .footer-brand b,.site-footer .footer-brand strong{font:700 var(--fs-sm) Manrope;letter-spacing:-.02em;color:var(--ink)}.site-footer .footer-brand strong{color:var(--green)}body.ops-page .site-footer{margin-top:0;padding:10px 34px}body:not(.ops-page)>main{flex:1}.preview-banner{background:#92400e;color:#fff;text-align:center;font:600 13px Manrope,system-ui,sans-serif;padding:4px 8px;position:sticky;top:0;z-index:1000}header{display:grid;grid-template-columns:auto 1fr auto auto auto;align-items:center;column-gap:18px;height:84px;flex-shrink:0;border-bottom:1px solid var(--line);padding:0 4.5vw;background:rgba(242,240,233,.84);backdrop-filter:blur(16px);position:sticky;top:0;z-index:6}header .brand{justify-self:start;width:227px;white-space:nowrap}header nav{display:flex;gap:18px;align-items:center;min-width:0;overflow:hidden;margin:0;flex-wrap:nowrap;height:28px}header nav a.nav-link{white-space:nowrap;flex:none}header nav .nav-ops{display:flex;gap:18px;align-items:center}header nav .nav-ops[hidden]{display:none}header .session-cost{justify-self:end;min-width:150px;text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}header .session-cost.balance{min-width:140px}header .session-cost[hidden]{display:block;visibility:hidden}header .session-cost:empty{display:block}header .session-cost:empty::after{content:\"\u00a0\";visibility:hidden}header #identity{justify-self:end;min-width:90px;display:flex;justify-content:flex-end}.nav-menu{display:none;justify-self:end;width:38px;height:38px;border:1px solid var(--line);border-radius:10px;background:var(--card);font-size:17px;line-height:1;color:var(--ink)}@media (max-width:1700px){header{padding:0 2.5vw;column-gap:12px}header nav{gap:12px}header .brand small{display:none}header .brand{width:110px}}header.nav-compact .nav-menu{display:block}header.nav-compact nav{height:auto;position:absolute;top:84px;right:4.5vw;flex-direction:column;align-items:stretch;gap:2px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:10px 14px;box-shadow:0 14px 40px rgba(26,42,34,.16);display:none;z-index:7}header.nav-compact nav.open{display:flex}header.nav-compact nav .nav-ops{flex-direction:column;align-items:stretch;gap:2px;border-top:1px solid var(--line);margin-top:6px;padding-top:6px}@media (max-width:900px){header nav{height:auto;position:absolute;top:84px;right:4.5vw;flex-direction:column;align-items:stretch;gap:2px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:10px 14px;box-shadow:0 14px 40px rgba(26,42,34,.16);display:none;z-index:7}header nav.open{display:flex}header nav .nav-ops{flex-direction:column;align-items:stretch;gap:2px;border-top:1px solid var(--line);margin-top:6px;padding-top:6px}header nav .nav-ops[hidden]{display:none}.nav-menu{display:block}}@media (max-width:640px){header{height:auto;min-height:84px;grid-template-columns:auto 1fr auto;padding:10px 20px;row-gap:8px}header .session-cost{grid-column:1/-1;min-width:0;text-align:center;justify-self:stretch}header .session-cost.balance{grid-column:1/-1}header #identity{min-width:56px}header nav{top:100px;right:20px}}", "private": ".private-workspace main{max-width:1400px;padding-top:32px}.operator-header{height:78px}.operator-intro h1{font-size:42px;letter-spacing:-.04em;margin:8px 0}.operator-intro p{max-width:780px;color:var(--muted)}@media (max-width:650px){.private-workspace main{padding:24px 16px}.operator-header{padding:0 16px}.operator-intro h1{font-size:32px}.private-workspace .operator-header .badge{display:none}}.operator-header>.badge{flex:none;margin-left:auto}.private-workspace .operator-header .brand span{flex:none}.private-workspace .operator-intro .badge{display:inline-block}.operator-bottom-bar{display:flex;justify-content:space-between;gap:18px;flex-wrap:wrap;padding:20px max(16px,calc((100vw - 1320px)/2));border-top:1px solid var(--line);color:var(--muted);font-size:13px}.operator-bottom-bar a{color:var(--ink)}.operator-bottom-bar span{overflow-wrap:anywhere;min-width:0}.private-workspace{padding-bottom:200px}.operator-bottom-bar{position:fixed;bottom:0;left:0;right:0;background:var(--paper);z-index:7;padding:10px 16px;gap:8px 18px}.operator-bottom-bar .operation-status{flex-basis:100%}.private-workspace main.account-page{padding:54px 4.5vw 80px;max-width:1500px}@media(max-width:850px){.private-workspace main.account-page{padding:35px 20px}}", "/assets/purchase.css": "body:has(.purchase) header{background:#f2f0e9;color:#17211d}body:has(.purchase) main{max-width:1130px;margin:auto}body:has(.purchase) header{position:sticky;top:0;z-index:4}"};
const flowStyles=`
/* One document scroll. Headings and navigation never stack as sticky layers. */
html,body{height:auto!important;min-height:100%;overflow-y:visible!important}
header,.page-head,.detail-head,.compact-head,.operator-tabs,.private-workspace .tabs,.preview-banner{position:relative!important;top:auto!important}
main.has-shared-actions{padding-bottom:calc(var(--shared-action-reserve,0px) + 28px)!important}
body.ops-page #app,body.ops-page #frame{height:auto!important;min-height:0;flex:none!important}
body.ops-page #panels{grid-template-rows:auto auto!important;flex:none!important}
body.ops-page .panel,body.ops-page .scroll,.private-workspace .scroll,.private-workspace .force-plan{height:auto!important;max-height:none!important;overflow-y:visible!important}
.shared-action-strip.readonly,.shared-action-strip.historical-review{position:static;box-shadow:none}

.gate-controls{position:static!important;max-height:none!important;overflow:visible!important;margin-top:24px}
.decision-options,.decision-body,.gate-evidence,.gate-artifact,.gate-structured,.context-body,.qa-history,.qa-items,.live-console,.console,.artifact-readable,.json-view,.feedback-context,.decision-scroll,.artifact-content,#node-detail{max-height:none!important;height:auto!important;overflow-y:visible!important}
.shared-action-strip{position:fixed;left:0;right:0;bottom:0;z-index:7;padding:10px max(20px,4.5vw) calc(10px + env(safe-area-inset-bottom));background:var(--card,#f5f5ee);border-top:1px solid var(--line,#d7ddd4);box-shadow:0 -5px 20px #17211d12;display:flex;flex-wrap:wrap;align-items:center;gap:8px 20px}
.shared-action-strip .gate-page-actions,.shared-action-strip>.actions{display:flex!important;gap:10px;margin:0!important;flex:1;align-items:center}
.shared-action-strip .gate-page-actions .primary,.shared-action-strip>.actions .primary{order:-1;flex:1}
.shared-action-strip .decision-checklist{margin:0;padding:6px 10px;flex:1;min-width:200px}
.shared-action-strip [aria-live]{margin:0;max-width:100%;overflow-wrap:anywhere}
.shared-action-strip .decision-launch{position:static!important;margin:0;padding:8px 16px;min-height:46px;width:auto;flex:1}
.shared-action-strip .decision-launch small,.shared-action-strip .decision-launch em{display:none}
.shared-decision-summary{padding:12px 16px;margin:10px 0;border:1px solid var(--line);background:var(--card);border-radius:12px}
.decision-secondary{margin:12px 0;border:1px solid var(--line);border-radius:12px;padding:12px 16px}.decision-secondary>summary{cursor:pointer;font-weight:600}
.decision-secondary label{display:block;margin-top:12px}
.notice.info,.gate-status.info{background:var(--card,#f5f5ee)!important;border:1px solid var(--line,#d7ddd4)!important;color:var(--muted,#52645a)!important;border-radius:12px;padding:12px 16px}
.gate-status.info{font:inherit;box-shadow:none!important;animation:none!important}
footer:has(.operation-status){position:static!important}
footer>.operation-status{position:static!important;max-width:100%;background:var(--card);padding:10px 14px;border:1px solid var(--line);border-radius:10px}
@media(max-width:700px){.shared-action-strip{gap:6px;padding:8px 16px calc(8px + env(safe-area-inset-bottom))}.shared-action-strip .gate-page-actions,.shared-action-strip>.actions{width:100%;flex-basis:100%}.shared-action-strip .decision-checklist{font-size:12px;padding:3px 8px}.shared-action-strip button{min-height:44px}.shared-action-strip .decision-launch{font-size:12px}footer>.operation-status{font-size:12px}}
@media(max-height:500px){.shared-action-strip{position:static;box-shadow:none;padding:10px;margin-top:16px}main{padding-bottom:30px}}
`;
let flowWired=false;
function wirePageFlow(){
 if(flowWired)return;flowWired=true;
 const measured=new WeakSet();
 const reserve=()=>{
  const main=document.querySelector('main');if(!main)return;
  const strips=[...main.querySelectorAll('.shared-action-strip')].filter(s=>getComputedStyle(s).position==='fixed'&&s.getClientRects().length);
  main.classList.toggle('has-shared-actions',strips.length>0);
  const height=Math.max(0,...strips.map(s=>s.getBoundingClientRect().height));
  main.style.setProperty('--shared-action-reserve',height+'px');
 };
 const sizes=new ResizeObserver(reserve);addEventListener('resize',reserve);
 const refresh=()=>{
  const main=document.querySelector('main');if(!main)return;
  for(const note of document.querySelectorAll('.notice:not(.info)')){
   const text=note.textContent.trim();
   if(/^(Your session cost and credits left stay in the top bar while it runs\.|Closed gates are real pauses\.|Historical gate review - read-only\.|Historical decision review - read-only\.|Current gate review - not waiting for a decision\.|No artifacts are available yet\.|No saved artifacts are available for this task\.|Gate decision saved\. Starting |Saved artifacts remain available below\.)/.test(text))note.classList.add('info');
  }
  for(const footer of main.querySelectorAll('.semantic-footer:not(.shared-action-strip)'))footer.classList.add('shared-action-strip');
  for(const form of main.querySelectorAll('form:not(#gateform)')){
   const primary=form.querySelector('button[type="submit"].primary');if(!primary||form.querySelector('.shared-action-strip'))continue;
   if(!form.closest('.decision-page'))continue;
   const strip=document.createElement('div');strip.className='shared-action-strip';strip.setAttribute('aria-label','Current decision actions');
   const actions=primary.closest('.actions');if(actions)strip.append(actions);else strip.append(primary);
   for(const status of form.querySelectorAll('#review-status'))strip.append(status);
   form.append(strip);
  }
  const actions=[...main.querySelectorAll('.decision-launch')].filter(x=>!x.closest('.shared-action-strip'));
  if(actions.length&&!main.querySelector('.gate-controls')){
   let strip=main.querySelector('.shared-action-strip');if(!strip){strip=document.createElement('div');strip.className='shared-action-strip';strip.setAttribute('aria-label','Current decision actions');main.append(strip)}
   strip.append(...actions);
  }
  // Historical reviews keep their original disabled controls, but must not advertise
  // a current decision or pin those controls over current evidence.
  const historical=[...main.querySelectorAll('.notice')].some(n=>/^(Historical (?:gate |decision )?review - read-only\.|Current gate review - not waiting for a decision\.)/.test(n.textContent.trim()));
  for(const strip of main.querySelectorAll('.shared-action-strip'))strip.classList.toggle('historical-review',historical);
  const waiting=main.querySelector('.shared-action-strip:not(.readonly):not(.historical-review)');let summary=main.querySelector('.shared-decision-summary');
  if(waiting&&!summary){summary=document.createElement('div');summary.className='shared-decision-summary';summary.setAttribute('role','status');summary.textContent='Your decision is needed. Use the decision action below.';const heading=main.querySelector('.page-head');heading?heading.after(summary):main.prepend(summary)}
  if(!waiting&&summary)summary.remove();
  for(const strip of main.querySelectorAll('.shared-action-strip'))if(!measured.has(strip)){measured.add(strip);sizes.observe(strip)}
  reserve();
 };
 new MutationObserver(refresh).observe(document.body,{childList:true,subtree:true});refresh();
}
const PUBLIC_LINKS=[{href:'/',label:'Home'},{href:'/credits',label:'Add Credits'},{href:'/about',label:'About'}];

function isCurrent(href,path,hash){
  if(href.startsWith('/ops'))return path==='/ops'&&(href.includes('#')?href.split('#')[1]===(hash.slice(1)||'health'):true);
  if(href==='/')return path==='/';
  return path===href||path.startsWith(href+'/');
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

export class Page{
static renderer(v){({render,html,useState,useEffect}=v)}

static installStyles(){
 if(!document.getElementById('ui-page-flow')){const flow=document.createElement('style');flow.id='ui-page-flow';flow.textContent=flowStyles;document.head.append(flow)}
 wirePageFlow();
 for(const [href,css] of Object.entries(pageStyles)){
  const link=[...document.querySelectorAll('link[rel=stylesheet]')].find(x=>(href==='private'?x.hasAttribute('data-page-private'):x.getAttribute('href')===href));
  if(!link||document.getElementById('ui-page-'+href.split('/').at(-1)))continue;
  const style=document.createElement('style');style.id='ui-page-'+href.split('/').at(-1);style.textContent=css;link.after(style);
 }
}
static main(placeholder,{id=null}={}){
 if(!placeholder)return document.querySelector('main');
 if(placeholder.tagName==='MAIN')return placeholder;
 const main=document.createElement('main');if(id!==false)main.id=id||'app';
 main.append(...placeholder.childNodes);placeholder.replaceWith(main);return main;
}
static heading(content,{classes='page-head'}={}){return '<div class="'+classes+'">'+content+'</div>'}
static footer(host,{classes='site-footer'}={}){const footer=document.createElement('footer');footer.className=classes;host.append(footer);return footer}
static mountFrame({headerClass=''}={}){
 Page.installStyles();const content=document.querySelector('#page-banner-content');
 const header=document.createElement('header');header.className=headerClass;header.append(...content.childNodes);content.replaceWith(header);
 const main=Page.main(document.querySelector('#page-outlet'),{id:false});
 const intro=main.querySelector('[data-page-intro-content]');if(intro){const shell=document.createElement('div');shell.className='operator-intro';shell.append(...intro.childNodes);intro.replaceWith(shell)}
 return main;
}
static epoch=0;static cleanups=[];static controllers=[];
static onExit(fn){Page.cleanups.push(fn)}
static controller(){const c=new AbortController();Page.controllers.push(c);return c}
static leave(){Page.epoch++;const cleanups=Page.cleanups,controllers=Page.controllers;Page.cleanups=[];Page.controllers=[];for(const fn of cleanups){try{fn()}catch(e){console.warn('[Requi.re] page cleanup failed',e)}}for(const c of controllers){try{c.abort()}catch{}}}
static shellView({initial}){
  const [state,setState]=useState({auth:null,path:location.pathname,hash:location.hash,menuOpen:false,runtime:null});
  useEffect(()=>{window.__shellSetState=(patch)=>setState(s=>({...s,...patch}));return ()=>{delete window.__shellSetState}},[]);
  const auth=state.auth,admin=!!(auth&&auth.authenticated&&auth.admin);
  const opsLinks=OPERATOR_PAGES.map(p=>({href:'/ops#'+p.view,label:p.label,tip:p.tip,operator:true}));
  const links=[...PUBLIC_LINKS,...opsLinks];
  const link=(l)=>html`<a key=${l.href} class="nav-link" data-operator=${l.operator?l.operator:null} href=${l.href}
    title=${l.tip||null} aria-current=${isCurrent(l.href,state.path,state.hash)?'page':null}>${l.label}</a>`;
  return html`
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

static mountShell(config){
  Page.installStyles();Page.main(document.querySelector('#page-outlet'));if(!document.querySelector('#app').hasChildNodes())document.querySelector('#app').innerHTML='<div class="loading" role="status">Loading workspace…</div>';
  const top=document.querySelector('#shell-top'),bottom=document.querySelector('#shell-bottom');
  render(null,top);render(null,bottom);
  // One Preact tree owns the whole shell so header and footer share state; the outlet (main#app)
  // sits between them in the static HTML and is not part of the tree.
  const host=document.createElement('div');host.id='shell-tree';
  top.append(host);
  render(html`<${Page.shellView} initial=${config}/>`,host);
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

}
