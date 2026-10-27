// Availability is evidence, not an empty payload. Never retain raw error text.
const lastSuccess=new Map();
export async function readSection(name,read,excluded=false){
 if(excluded)return {availability:'policy_excluded',reason:'Not collected by this mode policy',last_success_at:null};
 try{const data=await read(),at=new Date().toISOString();if(!lastSuccess.has(name)&&lastSuccess.size>=128)lastSuccess.delete(lastSuccess.keys().next().value);lastSuccess.set(name,at);return {availability:'available',last_success_at:at,data}}
 catch{return {availability:'unavailable',category:'read_failed',last_success_at:lastSuccess.get(name)||null}}
}
