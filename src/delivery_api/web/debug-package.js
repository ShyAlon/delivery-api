// Collection outcomes are data. Never confuse unavailable with empty evidence.
export async function collectDebugEvidence({taskId,operator,cloud,api}){
 const started_at=new Date().toISOString(),began=performance.now();
 const result=await api('/api/debug/collection'+(taskId?'?task_id='+encodeURIComponent(taskId):''));
 return {version:2,task_id:taskId||null,started_at,ended_at:new Date().toISOString(),duration_ms:Math.round(performance.now()-began),scope:result.scope,sections:result.sections};
}
