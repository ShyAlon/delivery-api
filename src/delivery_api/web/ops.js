(function(){
'use strict';
const $=s=>document.querySelector(s);
const el=(tag,props,...kids)=>{const n=document.createElement(tag);Object.assign(n,props||{});kids.forEach(k=>n.append(k));return n};
const dur=s=>{s=Math.max(0,Math.floor(s));const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);return d?`${d}d ${h}h ${m}m`:h?`${h}h ${m}m`:m?`${m}m ${s%60}s`:`${s}s`};
const num=(v,u)=>v==null?'Not Available':(typeof v==='number'?(Math.round(v*10)/10).toLocaleString():v)+(u||'');

// A table with a filter box, sortable headers, paging, and a scrolling body only. cols: [{key,label,tip,num,fmt}]
function makeTable(root,title,tip,cols,emptyText){
  const st={rows:[],sort:null,dir:1,page:0,size:10,q:''};
  const h2=el('h2',{textContent:title,title:tip});
  const q=el('input',{type:'search',placeholder:'Filter',title:'Show only rows that contain this text in any column.'});
  const size=el('select',{title:'How many rows to show per page.'});
  [10,25,50].forEach(n=>size.append(el('option',{value:n,textContent:n+' Per Page'})));
  const tools=el('div',{className:'tools'},q,size);
  const scroll=el('div',{className:'scroll'});
  const prev=el('button',{className:'secondary',textContent:'Previous'}),next=el('button',{className:'secondary',textContent:'Next'});
  const info=el('span',{className:'grow'});
  const pager=el('div',{className:'pager'},info,prev,next);
  root.replaceChildren(h2,tools,scroll,pager);
  const text=(r,c)=>{const v=r[c.key];return v==null?'':String(c.fmt?c.fmt(v,r):v)};
  function draw(){
    const needle=st.q.toLowerCase();
    let rows=st.rows.filter(r=>!needle||cols.some(c=>text(r,c).toLowerCase().includes(needle)));
    if(st.sort){const c=cols.find(c=>c.key===st.sort);rows=rows.slice().sort((a,b)=>{const x=a[c.key],y=b[c.key];if(x==null)return 1;if(y==null)return -1;return (typeof x==='number'&&typeof y==='number'?x-y:String(x).localeCompare(String(y)))*st.dir})}
    const pages=Math.max(1,Math.ceil(rows.length/st.size));st.page=Math.min(st.page,pages-1);
    const view=rows.slice(st.page*st.size,(st.page+1)*st.size);
    const tr=el('tr');
    cols.forEach(c=>{const arrow=st.sort===c.key?(st.dir>0?' \u25B2':' \u25BC'):'';
      const th=el('th',{textContent:c.label+arrow,title:c.tip+' Click to sort.',className:c.num?'num':''});
      th.onclick=()=>{if(st.sort===c.key)st.dir=-st.dir;else{st.sort=c.key;st.dir=1}draw()};tr.append(th)});
    const body=el('tbody');
    view.forEach(r=>body.append(el('tr',{},...cols.map(c=>el('td',{textContent:text(r,c),className:c.num?'num':''})))));
    const table=el('table',{},el('thead',{},tr),body);
    scroll.replaceChildren(rows.length?table:el('div',{className:'empty',textContent:st.rows.length?'No rows match the filter.':emptyText}));
    if(rows.length){scroll.replaceChildren(table)}
    info.textContent=rows.length?`Showing ${st.page*st.size+1} to ${st.page*st.size+view.length} of ${rows.length}`:'No rows';
    prev.disabled=st.page<=0;prev.title=prev.disabled?'You are on the first page.':'Go to the previous page.';
    next.disabled=st.page>=pages-1;next.title=next.disabled?'You are on the last page.':'Go to the next page.';
  }
  q.oninput=()=>{st.q=q.value;st.page=0;draw()};size.onchange=()=>{st.size=+size.value;st.page=0;draw()};
  prev.onclick=()=>{st.page--;draw()};next.onclick=()=>{st.page++;draw()};
  return {set(rows){st.rows=rows;draw()}};
}

const sessions=makeTable($('#p-sessions'),'Signed-In Users','People who made a request in the last 15 minutes. Visible to administrators only.',[
  {key:'user',label:'User',tip:'The email the person signed in with.'},
  {key:'seconds_ago',label:'Last Seen',tip:'How long ago this person last made a request.',num:true,fmt:v=>dur(v)+' ago'},
  {key:'running',label:'Running',tip:'The run this person has going right now, by phase. None means no run.'}],
  'Nobody has been seen in the last 15 minutes.');
const running=makeTable($('#p-running'),'Running Now','Phases that are using a slot on this server right now.',[
  {key:'run',label:'Run',tip:'A running phase. Names are numbered, not task ids.'},
  {key:'state',label:'State',tip:'Running, Stopping after a stop request, or Past Time Limit.'},
  {key:'seconds',label:'Running For',tip:'How long this phase has held its slot.',num:true,fmt:v=>dur(v)},
  {key:'peak_memory_mb',label:'Peak Memory',tip:'Largest memory this phase has used so far, in megabytes.',num:true,fmt:v=>num(v,' MB')}],
  'Nothing is running right now.');
const recent=makeTable($('#p-recent'),'Recent Runs','The latest finished and running phases with their CPU and memory.',[
  {key:'phase',label:'Phase',tip:'Which phase of the build ran.'},
  {key:'started',label:'Started (UTC)',tip:'When the run was first sampled, as month-day and time in UTC.',fmt:v=>v.slice(5,16)},
  {key:'ended',label:'Last Sample (UTC)',tip:'When the run was last sampled, as month-day and time in UTC.',fmt:v=>v.slice(5,16)},
  {key:'avg_cpu_percent',label:'Average CPU',tip:'Average CPU use as a percent of one core.',num:true,fmt:v=>num(v,'%')},
  {key:'max_cpu_percent',label:'Peak CPU',tip:'Highest CPU use as a percent of one core.',num:true,fmt:v=>num(v,'%')},
  {key:'cpu_seconds',label:'CPU Seconds',tip:'Total CPU time the run used. Compute credits are charged on this.',num:true,fmt:v=>num(v)},
  {key:'peak_memory_mb',label:'Peak Memory',tip:'Largest memory the run used, in megabytes.',num:true,fmt:v=>num(v,' MB')}],
  'No run has been measured yet.');

function card(k,v,s,tip){return el('div',{className:'card',title:tip},el('div',{className:'k',textContent:k}),el('div',{className:'v',textContent:v}),el('div',{className:'s',textContent:s||''}))}

function show(h){
  $('#asof').textContent='As of '+h.as_of;
  $('#flags').replaceChildren(...h.flags.map(f=>el('div',{className:'flag '+f.level,textContent:f.text})));
  const m=h.memory,c=h.cpu,cap=h.capacity;
  $('#cards').replaceChildren(
    card('Uptime',dur(h.uptime_seconds),'Since '+h.started,'How long this server process has been running.'),
    card('Signed-In Users',String(h.signed_in_users),`Seen in the last ${h.signed_in_window_minutes} minutes`,'People who made a request recently. Counts reset when the server restarts.'),
    card('Runs',`${cap.running} of ${cap.slots}`,cap.waiting?`${cap.waiting} waiting`:'None waiting','Phases using a slot, out of the slots this server has.'),
    card('Memory',m.used_percent==null?'Not Available':m.used_percent+'%',m.used_mb==null?'':`${m.used_mb} of ${m.total_mb} MB (${m.limited_by} limit)`,'Memory in use on this server, against its limit.'),
    card('CPU Now',c.container_percent==null?'Not Available':num(c.container_percent,'%'),`of one core, ${c.cpus||'?'} cores`,'Latest CPU reading, as a percent of one core.'),
    card('CPU Last 24 Hours',c.average_24h_percent==null?'Not Available':num(c.average_24h_percent,'%'),c.peak_24h_percent==null?'':'Peak '+num(c.peak_24h_percent,'%'),'Average and peak CPU over the last 24 hours of samples.'),
    card('Time Limit',cap.time_limit_minutes+' min',cap.mode,'How long one phase may run before it is stopped.'));
  sessions.set(h.sessions);running.set(h.running);recent.set(h.recent_runs);
  $('#note').textContent=h.restart_note;
}

async function load(){
  try{
    const r=await fetch('/api/admin/operations/health',{credentials:'same-origin'});
    if(r.status===401||r.status===403){fail('This page is for administrators. Sign in with an administrator account, then reload.');return}
    if(!r.ok){fail('The server could not read its own numbers ('+r.status+'). Try Refresh in a moment.');return}
    show(await r.json());
  }catch(e){fail('Could not reach the server. Check your connection and press Refresh.')}
}
function fail(msg){$('#flags').replaceChildren(el('div',{className:'flag warning',textContent:msg}));$('#cards').replaceChildren();sessions.set([]);running.set([]);recent.set([])}

$('#refresh').onclick=load;
$('#dl').onclick=async()=>{
  const b=$('#dl');b.disabled=true;b.title='Preparing the file. This takes a moment.';
  try{
    const r=await fetch('/api/admin/operations/log-download?minutes='+$('#mins').value,{credentials:'same-origin'});
    if(!r.ok)throw new Error(String(r.status));
    const name=(/filename="([^"]+)"/.exec(r.headers.get('Content-Disposition')||'')||[])[1]||'requi-server-log.txt';
    const url=URL.createObjectURL(await r.blob());const a=el('a',{href:url,download:name});document.body.append(a);a.click();a.remove();URL.revokeObjectURL(url);
  }catch(e){$('#flags').prepend(el('div',{className:'flag warning',textContent:'The log could not be downloaded ('+e.message+'). Only administrators can download it.'}))}
  finally{b.disabled=false;b.title='Download the scrubbed server log as a text file. Secrets, emails and tokens are removed, and the download is recorded.'}
};
load();setInterval(load,10000);
})();
