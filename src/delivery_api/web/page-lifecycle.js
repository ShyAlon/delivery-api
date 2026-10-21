/* One active page at a time. The router runs the page's cleanups and aborts its in-flight
   requests before the next page mounts, and the epoch lets late async callbacks notice they
   belong to a page that is already gone. Mounts capture pageEpoch() at entry and re-check it
   with pageStale() immediately before every DOM commit: a mount that resolves after you
   navigated away never commits, never registers cleanups and never starts timers. */
let epoch=0;
let cleanups=[];
let controllers=[];
export function onPageExit(fn){cleanups.push(fn)}
export function pageEpoch(){return epoch}
export function pageStale(at){return at!==epoch}
export function pageController(){const c=new AbortController();controllers.push(c);return c}
export function leavePage(){
  epoch++;
  const fns=cleanups,ctrls=controllers;cleanups=[];controllers=[];
  for(const fn of fns){try{fn()}catch(e){console.warn('[Requi.re] page cleanup failed',e)}}
  for(const c of ctrls){try{c.abort()}catch(e){}}
}
