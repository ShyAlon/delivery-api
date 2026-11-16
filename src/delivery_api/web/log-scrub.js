// Scrubs text before it leaves the browser in a copied log. No DOM here so it can be tested on its own.
const SECRET_KEY='(?:password|passwd|secret|token|authorization|cookie|api[-_ ]?key|credential|session)';
const METRIC_COUNTS=new Set(['prompt_tokens','completion_tokens','reasoning_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens','cached_tokens','input_tokens','output_tokens','total_tokens','cache_hit_tokens','cache_miss_tokens','prompt_eval_count','eval_count']);
const JOIN_IDS=new Set(['logical_call_id','snapshot_sha256','answer_id','attempt_id','job_id','contribution_key']);
const METRIC_RATES=new Set(['tokens_per_s']);
export function safeNumericMetric(key,value){return typeof value==='number'&&Number.isFinite(value)&&value>=0&&(METRIC_RATES.has(key)||(METRIC_COUNTS.has(key)&&Number.isSafeInteger(value)))}
export function scrubText(value){
  let s=String(value??'');
  // Task paths and owner-qualified test/runtime IDs can repeat across JSON,
  // URLs and free-text logs. Redact every occurrence, not only standalone tokens.
  // Bare session UUIDs remain diagnostic identifiers outside task paths.
  const uuid='[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}';
  s=s.replace(new RegExp('(/tasks/)'+uuid+'(?:-[a-z0-9_-]+)?(?=[/?#\\s\\"\\x27]|$)','gi'),'$1[token]');
  s=s.replace(new RegExp('\\b'+uuid+'-[a-z0-9_-]+\\b','gi'),'[token]');
  s=s.replace(/Bearer\s+[A-Za-z0-9._~+\/=-]+/gi,'Bearer [redacted]');
  s=s.replace(/\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}/g,'[jwt]');
  s=s.replace(/\b(?:sk|pk|rk|sb)[-_][A-Za-z0-9_-]{12,}/g,'[key]');
  s=s.replace(/\b[a-z][a-z0-9+.-]*:\/\/[^\s/:@]+:[^\s/@]+@/gi,m=>m.split('://')[0]+'://[redacted]@');
  s=s.replace(/([?&][^=&\s"']+=)[^&\s"']+/g,'$1[redacted]');
  s=s.replace(new RegExp('(["\']?[A-Za-z0-9_.-]*'+SECRET_KEY+'[A-Za-z0-9_.-]*["\']?\\s*[:=]\\s*)("[^"]*"|\'[^\']*\'|[^\\s,;}]+)', 'gi'),(all,prefix,raw)=>{const key=prefix.split(/[:=]/,1)[0].trim().replace(/^["']|["']$/g,'');let value;try{value=JSON.parse(raw)}catch{}return safeNumericMetric(key,value)||((METRIC_COUNTS.has(key)||METRIC_RATES.has(key))&&raw.trim()==='null')?all:prefix+'[redacted]'});
  s=s.replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}/g,'[email]');
  s=s.replace(/\b[A-Fa-f0-9]{32,}\b/g,'[hex]');
  s=s.replace(/(?<![A-Za-z0-9_\/.-])[A-Za-z0-9_-]{40,}={0,2}(?![A-Za-z0-9_\/.-])/g,m=>/\d/.test(m)&&/[A-Za-z]/.test(m)?'[token]':m);
  return s;
}
/** One text report from the request list and browser events. No response bodies are ever included. */
export function buildClientReport({requests,records,startedAt,url,build,now}){
  const when=now||new Date().toISOString();
  const head=[`Requi.re client log`,`copied_at: ${when}`,`page: ${scrubText(url)}`,`build: ${scrubText(build||'unknown')}`,`range: this browser tab since ${startedAt} (last ${requests.length} requests, last ${records.length} browser events)`,''];
  const reqLines=requests.map(r=>scrubText(`${r.ts} ${r.status} ${r.method} ${r.url} ${r.total_ms} ms server ${r.server_ms===null||r.server_ms===undefined?'?':r.server_ms+' ms'} ref ${r.ref||'-'}`));
  const evLines=records.map(r=>scrubText(`${r.ts} ${r.kind} ${r.path||''} ${String(r.message||'').replace(/\s+/g,' ').slice(0,2000)}`));
  return {text:head.concat(['--- requests ---'],reqLines,['','--- browser events ---'],evLines).join('\n')+'\n',range:`this tab since ${startedAt}, ${requests.length} requests and ${records.length} browser events`};
}

/* Structured redaction for exported JSON: mirrors the server export's sensitive
   key set (no 'session' - session ids are diagnostic, not credentials) and keeps
   the JSON valid, then scrubs free-text values with scrubText. */
const SENSITIVE_JSON_KEY=/(password|passwd|passcode|secret|token|authorization|cookie|api[-_]?key|credential|private[-_]?key)/i;
export function scrubJson(value){
  if(Array.isArray(value))return value.map(scrubJson);
  if(value&&typeof value==='object'){
    const out={};
    for(const[k,v]of Object.entries(value))out[k]=JOIN_IDS.has(k)&&typeof v==='string'&&/^[a-zA-Z0-9:_-]{1,240}$/.test(v)?v:(METRIC_COUNTS.has(k)||METRIC_RATES.has(k))?(v===null||safeNumericMetric(k,v)?v:'[redacted]'):SENSITIVE_JSON_KEY.test(k)?'[redacted]':scrubJson(v);
    return out;
  }
  if(typeof value==='string')return scrubText(value);
  return value;
}
/* One exported part: JSON parts keep their structure redacted; anything else is plain text scrubbed. */
export function scrubPart(text){
  const s=String(text??'');
  try{return JSON.stringify(scrubJson(JSON.parse(s)),null,2)}catch{return scrubText(s)}
}
