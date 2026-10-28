const notices=new WeakMap();
export async function refreshResult(slot,read){
 try{const result=await read();if(!slot?.isConnected||result===false)return result;slot.dataset.lastSuccess=new Date().toISOString();notices.get(slot)?.remove();notices.delete(slot);return result}
 catch(error){if(slot?.isConnected){let notice=notices.get(slot);if(!notice){notice=document.createElement('div');notice.className='notice result-stale';notices.set(slot,notice);slot.before(notice)}notice.textContent=`Phase results stale - refresh failed. Last successful read: ${slot.dataset.lastSuccess||'not yet available'}. `;const retry=document.createElement('button');retry.className='secondary';retry.textContent='Retry results';retry.onclick=()=>refreshResult(slot,read).catch(()=>{});notice.append(retry)}throw error}
}
