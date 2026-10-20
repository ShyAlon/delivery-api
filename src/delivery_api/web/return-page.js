// Where to go after signing in. Only the fixed operator and credits pages come back; anything else (another site, a protocol-relative link) goes home.
export const RETURN_PAGES=['/ops','/credits'];
export function returnPage(search){
  const next=new URLSearchParams(search||'').get('next');
  return RETURN_PAGES.includes(next)?next:'/';
}
