import {deliverText} from './client-core.js';

/* One hover "Copy" button for every dark text block (Debug dialog sections, live output, artifact text, required-input code).
   The button floats over the block under the pointer, so no block is wrapped or re-laid out and blocks added later work with no setup. */
export const COPY_BLOCKS='.json-view,.console,.live-console,.artifact-content,.input-requirement code';

export function startCopyBlocks(){
 if(document.querySelector('#copyblock'))return ()=>{};
 const button=document.createElement('button');
 button.id='copyblock';button.type='button';button.className='copy-block hidden';button.textContent='Copy';
 button.setAttribute('aria-label','Copy this text');button.setAttribute('aria-live','polite');
 document.body.appendChild(button);
 let target=null,hideTimer=null,busy=false,resetTimer=null;
 const hide=()=>{if(busy)return;target=null;button.classList.add('hidden')};
 const place=()=>{
  if(!target||!target.isConnected){target=null;button.classList.add('hidden');return}
  const box=target.getBoundingClientRect();
  if(box.bottom<=0||box.top>=innerHeight||box.right<=0||box.left>=innerWidth){button.classList.add('hidden');return}
  button.classList.remove('hidden');
  button.style.top=`${Math.max(box.top,0)+8}px`;
  button.style.left=`${Math.max(Math.min(box.right,innerWidth)-button.offsetWidth-12,0)}px`;
 };
 const over=event=>{
  const block=event.target instanceof Element?event.target.closest(COPY_BLOCKS):null;
  if(event.target===button){clearTimeout(hideTimer);return}
  if(!block){clearTimeout(hideTimer);hideTimer=setTimeout(hide,250);return}
  clearTimeout(hideTimer);
  if(!busy){target=block;if(!resetTimer)button.textContent='Copy'}
  place();
 };
 button.addEventListener('pointerenter',()=>clearTimeout(hideTimer));
 button.addEventListener('pointerleave',()=>{clearTimeout(hideTimer);hideTimer=setTimeout(hide,250)});
 button.addEventListener('click',async()=>{
  if(busy||!target)return;
  const text=target.textContent||'';
  busy=true;button.disabled=true;button.textContent='Copying…';clearTimeout(resetTimer);resetTimer=null;
  try{button.textContent=(await deliverText(text,'requi-text.txt'))==='copied'?'Copied':'Downloaded'}
  catch{button.textContent='Copy failed'}
  finally{
   button.disabled=false;busy=false;
   resetTimer=setTimeout(()=>{resetTimer=null;if(!busy)button.textContent='Copy'},1800);
  }
 });
 document.addEventListener('pointerover',over,{passive:true});
 document.addEventListener('scroll',place,{capture:true,passive:true});
 window.addEventListener('resize',place);
 return ()=>{document.removeEventListener('pointerover',over);document.removeEventListener('scroll',place,{capture:true});window.removeEventListener('resize',place);button.remove()};
}
