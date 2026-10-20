// The navigation bar and footer that every page shares (the app and the operator pages). The header markup itself is in each page's HTML,
// identical; this file adds what depends on who is signed in (the operator pages, administrators only), the current-page mark, and the footer.
export const OPERATOR_PAGES=[
  {view:'health',label:'Server Health',tip:'Uptime, runs, CPU, memory and who is signed in.'},
  {view:'stops',label:'Admin Stops',tip:'Every time a run was stopped, by whom, and whose run it was.'},
  {view:'spend',label:'Credits And Spend',tip:'Credits left and used for every account.'},
  {view:'grant',label:'Grant Credits',tip:'Give credits to an account and see the recent grants.'},
];
const onOps=()=>location.pathname==='/ops'||location.pathname==='/ops/';
const opsView=()=>{const n=location.hash.slice(1);return OPERATOR_PAGES.some(p=>p.view===n)?n:'health'};

export function markCurrentPage(){
  document.querySelectorAll('header nav a.nav-link').forEach(a=>{
    const url=new URL(a.href,location.href);
    let current=false;
    if(url.pathname==='/ops')current=onOps()&&url.hash.slice(1)===opsView();
    else if(url.pathname==='/')current=location.pathname==='/';
    else current=location.pathname===url.pathname||location.pathname.startsWith(url.pathname+'/');
    if(current)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');
  });
}

export function addOperatorLinks(nav,auth){
  if(!nav||!auth||!auth.authenticated||!auth.admin||nav.querySelector('[data-operator]'))return false;
  for(const p of OPERATOR_PAGES){
    const a=document.createElement('a');
    a.className='nav-link';a.dataset.operator=p.view;a.href='/ops#'+p.view;a.textContent=p.label;a.title=p.tip;nav.append(a);
  }
  markCurrentPage();return true;
}

export function addFooter(runtimeId){
  let footer=document.querySelector('footer.site-footer');
  if(!footer){
    footer=document.createElement('footer');footer.className='site-footer';
    footer.innerHTML='<span class="footer-brand"><b>Requi</b><strong>.re</strong></span><span class="footer-build" title="The version of the server you are using."></span><span class="grow"></span><a class="nav-link" href="/about">About</a><a class="nav-link" href="/credits">Add Credits</a>';
    document.body.append(footer);
  }
  footer.querySelector('.footer-build').textContent='Build '+(runtimeId?String(runtimeId).slice(0,7):'unknown');
}

async function start(){
  markCurrentPage();
  window.addEventListener('hashchange',markCurrentPage);
  addFooter(null);
  try{const live=await (await fetch('/health',{cache:'no-store'})).json();addFooter(live.runtime_id)}catch(e){}
  try{const auth=await (await fetch('/api/auth',{credentials:'same-origin'})).json();addOperatorLinks(document.querySelector('header nav'),auth)}catch(e){}
}
if(typeof document!=='undefined'&&document.querySelector('header nav'))start();
