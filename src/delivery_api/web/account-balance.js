// The header's credit balance text. No DOM here so it can be tested on its own.
export function balanceLabel(reply){
  if(!reply||reply.credits===null||reply.credits===undefined)return null;
  if(!Number.isFinite(reply.credits))throw new Error('Balance unavailable');
  return `Balance ${reply.credits.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})} credits`;
}

// The balance tooltip: the balance, then the latest top-up (amount and a readable date), or a plain statement that none is recorded.
export function balanceTitle(reply){
  if(!reply||reply.credits===null||reply.credits===undefined)return '';
  const fmt=n=>n.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
  let text=`Balance ${fmt(reply.credits)} credits`;
  const t=reply.last_topup;
  if(t&&Number.isFinite(t.credits)&&Number.isFinite(t.at))text+=`\nLast top-up: +${fmt(t.credits)} credits on ${new Date(t.at*1000).toLocaleString(undefined,{dateStyle:'medium',timeStyle:'short'})}`;
  else text+='\nNo top-up recorded yet';
  if(!(reply.credits>0))text+='\nNo credits left. Ask for more credits.';
  return text;
}
