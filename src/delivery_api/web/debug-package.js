// Collection outcomes are data. Never confuse unavailable with empty evidence.
export async function collectDebugEvidence({taskId,operator,cloud,api}){
 const sections=[],started_at=new Date().toISOString();
 const collect=async(source,url,enabled=true)=>{
  if(!enabled){sections.push({source,availability:'policy_excluded'});return}
  try{const data=await api(url);sections.push({source,availability:'available',collected_at:new Date().toISOString(),data})}
  catch{sections.push({source,availability:'unavailable',category:'read_failed',next:'Retry collection in Debug; missing evidence is not an empty result.'})}
 };
 const task=taskId?encodeURIComponent(taskId):null;
 for(const [source,url] of [['credits','/api/account/ledger'],['credit-history','/api/account/credit-history'],['startup-checks','/api/debug/preflight']])await collect(source,url);
 for(const [source,url] of [['request-timing','/api/admin/operations/request-costs'],['server-capacity','/api/admin/operations/capacity']])await collect(source,url,operator&&cloud);
 await collect('log-durability','/api/debug/log-durability',operator);
 await collect('durable-diagnostics','/api/debug/evidence'+(task?'?task_id='+task:''),operator);
 for(const [source,path] of [['db-task-state','task-debug'],['db-state-events','task-logs'],['db-start-failures','task-failure-diagnostics'],['db-usage','task-usage']]){
  await collect(source,`/api/operator/${path}?task_id=${task}${path==='task-failure-diagnostics'?'&limit=20':''}`,!!task&&operator&&cloud);
 }
 return {version:2,task_id:taskId||null,started_at,ended_at:new Date().toISOString(),
  scope:taskId?'selected task; account credits/history and process health are not task-filtered':'operator process evidence; select a task for DB evidence',
  limits:'Each source keeps its own bounded page/window and truncation flags. No historical reconstruction or raw PostgreSQL server logs.',sections};
}
