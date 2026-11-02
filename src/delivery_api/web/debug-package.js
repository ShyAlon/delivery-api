// Collection outcomes are data. Never confuse unavailable with empty evidence.
export async function collectDebugEvidence({taskId,operator,cloud,api}){
 const started_at=new Date().toISOString(),began=performance.now();
 const result=await api('/api/debug/collection'+(taskId?'?task_id='+encodeURIComponent(taskId):''));
 return {version:2,task_id:taskId||null,started_at,ended_at:new Date().toISOString(),duration_ms:Math.round(performance.now()-began),scope:result.scope,sections:result.sections};
}

// Two groups at most: do not multiply the database pool budget with an
// unbounded Promise.all. Each HTTP request owns a separate scoped connection.
export async function collectExportGroups(groups,onProgress=()=>{}){
 const result=new Array(groups.length);let next=0;
 async function worker(){while(next<groups.length){const index=next++,began=performance.now();try{onProgress(index,true);result[index]={value:await groups[index](),duration_ms:Math.round(performance.now()-began)}}catch(error){result[index]={error,duration_ms:Math.round(performance.now()-began)}}finally{onProgress(index,false)}}}
 await Promise.all(Array.from({length:Math.min(2,groups.length)},worker));return result;
}
