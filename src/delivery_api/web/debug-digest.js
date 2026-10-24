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
