// The header's credit balance text. No DOM here so it can be tested on its own.
export function balanceLabel(reply){
  if(!reply||reply.credits===null||reply.credits===undefined)return null;
  if(!Number.isFinite(reply.credits))throw new Error('Balance unavailable');
  return `Balance ${reply.credits.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})} credits`;
}
