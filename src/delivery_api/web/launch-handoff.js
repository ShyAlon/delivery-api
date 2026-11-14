/* UI handoff only. The synchronous POST continues; never resend or infer success from time. */
export async function launchHandoff({send,readTask,phase,afterId,onLaunched,stillCurrent=()=>true,interval=2000}){
 let settled=false,handed=false,timer;
 const launched=async()=>{if(handed||!stillCurrent())return;handed=true;await onLaunched()};
 const request=Promise.resolve().then(send).then(async result=>{settled=true;clearTimeout(timer);await launched();return result},error=>{settled=true;clearTimeout(timer);throw error});
 const poll=async()=>{
  if(settled||handed||!stillCurrent())return;
  try{
   const task=await readTask();
   if(!settled&&stillCurrent()&&(task.events||[]).some(e=>Number(e.id)>afterId&&e.phase_key===phase&&e.kind==='command'))await launched();
  }catch{/* Transport read failure is not launch failure. The POST remains authoritative. */}
  if(!settled&&!handed&&stillCurrent())timer=setTimeout(poll,interval);
 };
 timer=setTimeout(poll,interval);
 try{return await request}finally{settled=true;clearTimeout(timer)}
}
