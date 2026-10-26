// The Debug dialog's bounded diagnostic summary. No DOM here so it can be tested on its own.
import {scrubText} from './log-scrub.js';

const FAILURE_KIND=/(error|fail)/i;

export function isFailureEvent(event){
  if(!event||typeof event!=='object')return false;
  if(FAILURE_KIND.test(String(event.kind||'')))return true;
  const status=event.payload&&event.payload.http_status;
  return typeof status==='number'&&status>=400;
}

const stamp=ts=>{const d=new Date(Number(ts)*1000);return Number.isFinite(d.getTime())?d.toISOString():'unknown-time'};

function eventLine(e){
  const text=`${e.kind||'event'}: ${String(e.message||'')}`.replace(/\s+/g,' ').trim();
  return `${stamp(e.ts)} [${e.phase_key||'-'}] ${scrubText(text)}`;
}

export function buildDiagnosticSummary({diag,runtime,url,now}){
  const when=now||new Date().toISOString();
  const task=(diag&&diag.task)||{},engine=(diag&&diag.engine)||{},ev=(diag&&diag.events)||{};
  const total=typeof ev.total==='number'?ev.total:null,shown=typeof ev.shown==='number'?ev.shown:null;
  const items=(ev.items||[]).slice().sort((a,b)=>((a.ts||0)-(b.ts||0))||((a.id||0)-(b.id||0)));
  const failures=items.filter(isFailureEvent);
  const lines=[
    'Requi.re diagnostic summary',
    `copied_at: ${scrubText(when)}`,
    `page: ${scrubText(url||'')}`,
    `build: ${scrubText(runtime||'unknown')}`,
    `task: ${scrubText(task.task_id||'')} ${scrubText(JSON.stringify(task.title||''))}`,
    '',
    '== Current state ==',
    `state: ${scrubText(task.state||'?')}   phase: ${scrubText(task.active_phase||'?')}   generation: ${task.generation??'?'}`,
    engine.available
      ?`engine: available, session ${scrubText(engine.session_id||'?')}, ${scrubText(engine.process_status_name||'')}, next ${scrubText(engine.next_step||'')}`
      :`engine: ${scrubText(engine.error||'unavailable')}`,
    ''];
  if(failures.length===0){
    lines.push('== Failure events ==','No failure events in the events available to this export.','');
  }else{
    lines.push('== Earliest available failure ==',eventLine(failures[0]),'');
    if(failures.length>1){
      lines.push(`== Later failure events (${failures.length-1}) ==`);
      for(const e of failures.slice(1))lines.push(eventLine(e));
      lines.push('');
    }
  }
  if(total!==null&&shown!==null){
    lines.push(total>shown
      ?`Event coverage: this export holds the last ${shown} of ${total} events, so an earlier failure may exist outside it.`
      :`Event coverage: this export holds all ${total} recorded events.`);
  }else{
    lines.push('Event coverage: unknown; the event history may be incomplete.');
  }
  lines.push('Events are quoted verbatim in chronological order; order alone does not establish cause.','');
  const recent=items.slice(-10);
  lines.push(`== Recent events (last ${recent.length}) ==`);
  for(const e of recent)lines.push(eventLine(e));
  return lines.join('\n')+'\n';
}

// Request-cost view (operator Debug dialog). Pure functions: no DOM, so node can test them.
const COST_METRICS=[['duration_ms','Duration'],['db_ms','DB'],['engine_ms','Engine'],['checkout_ms','Checkout'],['auth_ms','Auth'],['held_ms','Held'],['loop_blocked_ms','Loop Block']];

export function requestCostColumns(){
  const cols=[{key:'type',label:'Request'},{key:'count',label:'Requests'}];
  for(const [metric,name] of COST_METRICS)for(const p of ['p50','p95'])cols.push({key:`${p}_${metric}`,label:`${p.toUpperCase()} ${name} Ms`});
  return cols;
}

export function requestCostRows(data){
  return (data?.types||[]).map(t=>{
    const row={type:t.type,count:t.count};
    for(const [metric] of COST_METRICS)for(const p of ['p50','p95'])row[`${p}_${metric}`]=t[p]?.[metric]??0;
    return row;
  });
}

export function sortRequestCostRows(rows,key,ascending){
  const dir=ascending?1:-1;
  return [...rows].sort((a,b)=>{
    const av=a[key],bv=b[key];
    if(typeof av==='string'||typeof bv==='string')return String(av).localeCompare(String(bv))*dir;
    return ((av||0)-(bv||0))*dir;
  });
}

const COST_SLOWEST_COLUMNS=[['type','Request'],['status','Status'],['at','At'],['duration_ms','Duration Ms'],['db_ms','DB Ms'],['engine_ms','Engine Ms'],['checkout_ms','Checkout Ms'],['auth_ms','Auth Ms'],['held_ms','Held Ms'],['loop_blocked_ms','Loop Block Ms']];

export function requestCostSlowestColumns(){
  return COST_SLOWEST_COLUMNS.map(([key,label])=>({key,label}));
}

function _textTable(columns,rows){
  const widths=columns.map(([key,label])=>Math.max(label.length,...rows.map(r=>String(r[key]??'').length)));
  const line=columns.map(([key,label],i)=>label.padEnd(widths[i])).join('  ').trimEnd();
  const body=rows.map(r=>columns.map(([key],i)=>String(r[key]??'').padEnd(widths[i])).join('  ').trimEnd());
  return [line,...body].join('\n');
}

export function formatRequestCostsText(data){
  if(!data||!data.count)return 'No requests recorded in this window yet.';
  const cols=[['type','Request'],['count','Requests'],...COST_METRICS.flatMap(([m,n])=>[[`p50_${m}`,`P50 ${n} Ms`],[`p95_${m}`,`P95 ${n} Ms`]])];
  const head=`Request timing: ${data.count} requests since ${data.since||'start'} (this server process)`;
  return [head,_textTable(cols,requestCostRows(data)),'','Slowest Recent Requests',_textTable(COST_SLOWEST_COLUMNS,data.slowest||[])].join('\n');
}

// Startup preflight report (scripts/docker/preflight.py, read back via /api/debug/preflight).
// Pure: no DOM, so node can test it.
export function formatPreflightReport(report){
  if(!report)return 'no startup preflight report: this container predates it';
  const headline={pass:'PASS',warn:'PASS WITH WARNINGS',fail:'REFUSED TO START'}[report.status]||String(report.status||'unknown').toUpperCase();
  const stamp=report.generated_at?(' at '+report.generated_at):'';
  const lines=['status '+headline+stamp];
  for(const c of (report.checks||[])){
    let line=(c.status==='pass'?'pass ':c.status==='skip'?'skip ':String(c.status||'').padEnd(5))+(c.name||'');
    if(c.status!=='pass'&&c.status!=='skip'){
      if(c.checked)line+='\n    checked  '+(c.checked||'');
      if(c.expected)line+='\n    expected '+(c.expected||'');
      if(c.found)line+='\n    found    '+(c.found||'');
      if(c.where)line+='\n    where    '+(c.where||'');
      if(c.remedy)line+='\n    remedy   '+(c.remedy||'');
    }
    lines.push(line);
  }
  return lines.join('\n');
}
