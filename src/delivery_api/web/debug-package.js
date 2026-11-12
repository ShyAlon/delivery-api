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

// Full selected-task history, not the account ledger's newest 20 rows.
export async function collectTaskCosts({taskId,api}){
 const sources=['answers','answer_usage','provider_attempts','retail_contributions'],sections=[];
 for(const source of sources){
  let after='',complete=false;const rows=[],seen=new Set();let provenance=null;
  try{while(!complete){
   const page=await api(`/api/tasks/${encodeURIComponent(taskId)}/cost-evidence?source=${source}&after=${encodeURIComponent(after)}`);
   if(!Array.isArray(page.rows)||page.source!==source||page.task_id!==taskId)throw new Error('Invalid accounting page');
   rows.push(...page.rows);provenance=page.provenance;
   complete=page.complete===true;
   if(!complete){if(!page.next_cursor||seen.has(page.next_cursor))throw new Error('Accounting cursor did not advance');seen.add(page.next_cursor);after=page.next_cursor}
  }sections.push({source,rows,complete,provenance})}
  catch(error){sections.push({source,rows,complete:false,availability:'unavailable',error:String(error.message||error),provenance})}
 }
 return {task_id:taskId,scope:'Full recorded selected-task accounting history; sources can finish at different times during an active run',complete:sections.every(x=>x.complete),sections};
}
