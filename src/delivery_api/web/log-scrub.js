// Scrubs text before it leaves the browser in a copied log. No DOM here so it can be tested on its own.
const SECRET_KEY='(?:password|passwd|secret|token|authorization|cookie|api[-_ ]?key|credential|session)';
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
  s=s.replace(new RegExp('("?'+SECRET_KEY+'[A-Za-z_]*"?\\s*[:=]\\s*)("[^"]*"|\'[^\']*\'|[^\\s,;}]+)','gi'),'$1[redacted]');
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
const SENSITIVE_JSON_KEY=/(password|passcode|secret|token|authorization|cookie|api[-_]?key|credential|private[-_]?key)/i;
export function scrubJson(value){
  if(Array.isArray(value))return value.map(scrubJson);
  if(value&&typeof value==='object'){
    const out={};
    for(const[k,v]of Object.entries(value))out[k]=SENSITIVE_JSON_KEY.test(k)?'[redacted]':scrubJson(v);
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
