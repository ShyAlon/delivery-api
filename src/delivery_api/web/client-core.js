export const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export const time = timestamp => timestamp ? new Date(timestamp*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : '—';
export const fullTime = timestamp => timestamp ? new Intl.DateTimeFormat([], {year:'numeric',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',timeZoneName:'short'}).format(new Date(timestamp*1000)) : '—';

export function clientLog(kind,message,detail={}){
  const entry={ts:new Date().toISOString(),kind,message,path:location.pathname,...detail};
  (kind.includes('error')||kind.includes('failure')?console.error:console.info)(`[Requi.re ${entry.ts}] ${kind}: ${message}`,detail);
  fetch('/api/client-log',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({kind,message,path:location.pathname,detail}),keepalive:true}).catch(()=>{});
}

export async function api(url,opts={}){
  const method=(opts.method||'GET').toUpperCase(),timeout=opts.timeout===undefined?(method==='GET'?60000:null):opts.timeout,controller=timeout?new AbortController():null;
  let timedOut=false,timer=timeout?setTimeout(()=>{timedOut=true;controller.abort()},timeout):null,response;
  try{response=await fetch(url,{headers:{'content-type':'application/json'},...opts,signal:opts.signal||(controller&&controller.signal)})}
  catch(error){
    if(error.name==='AbortError'&&timedOut)throw new Error('Error: The request timed out.\nLikely cause: the server did not answer a read request within 60 seconds.\nNext: check the server log and retry the page load.');
    if(error.name==='AbortError')throw error;
    throw new Error(`Error: The server could not be reached.\nLikely cause: ${error.message||'The connection failed.'}\nNext: your text is preserved. Check the server is running, then retry.`);
  }finally{if(timer)clearTimeout(timer)}
  if(!response.ok){
    const raw=await response.text();let detail;
    try{detail=JSON.parse(raw).detail}catch{}
    const info=typeof detail==='object'?detail:{error:typeof detail==='string'?detail:raw};
    const message=`Error: ${info.error||'The request failed.'}\nLikely cause: ${info.cause||'The server could not complete this action.'}\nNext: ${info.next||'Retry once. If it fails again, open the task audit and server log.'}`;
    const error=new Error(message);error.status=response.status;error.info=info;error.endpoint=url;
    clientLog('api.error',message,{endpoint:url,http_status:response.status,task_state:info.current_state,active_phase:info.active_phase,decision_id:opts.decisionId});
    throw error;
  }
  return response.json();
}
