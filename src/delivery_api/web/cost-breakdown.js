// The session-cost tooltip: credits per phase, largest first. No DOM here so it can be tested on its own.
const fmt=n=>n.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
export function phaseLabel(key){
  const words=String(key||'').replace(/[_-]+/g,' ').trim();
  return words?words.charAt(0).toUpperCase()+words.slice(1):'Other';
}
export function costTitle(total,phases){
  const head='Rolling Session Cost';
  if(!Array.isArray(phases)||!phases.length)return head;
  const rows=phases.filter(p=>p&&Number.isFinite(p.credits)).map(p=>`${phaseLabel(p.phase)}: ${fmt(p.credits)} credits`);
  if(!rows.length)return head;
  return `${head}\n${rows.join('\n')}\nTotal: ${fmt(total)} credits`;
}
