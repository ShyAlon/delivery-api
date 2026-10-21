/* One active page at a time. The router runs the page's cleanups before the next page mounts,
   and the epoch lets late async callbacks notice they belong to a page that is already gone. */
let epoch=0;
let cleanups=[];
export function onPageExit(fn){cleanups.push(fn)}
export function pageEpoch(){return epoch}
export function leavePage(){
  epoch++;
  const fns=cleanups;cleanups=[];
  for(const fn of fns){try{fn()}catch(e){console.warn('[Requi.re] page cleanup failed',e)}}
}
