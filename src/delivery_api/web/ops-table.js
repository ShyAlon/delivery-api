// The operator tables: a filter box, sortable headers, paging, and a scrolling body only. Used by ops.js and tested on its own.
const el=(tag,props,...kids)=>{const n=document.createElement(tag);Object.assign(n,props||{});kids.forEach(k=>n.append(k));return n};
export {el};
export function makeTable(root,title,tip,cols,emptyText){
  const st={rows:[],sort:null,dir:1,page:0,size:10,q:''};
  const h2=el('h2',{textContent:title,title:tip});
  const q=el('input',{type:'search',placeholder:'Filter',title:'Show only rows that contain this text in any column.'});
  const size=el('select',{title:'How many rows to show per page.'});
  [10,25,50].forEach(n=>size.append(el('option',{value:n,textContent:n+' Per Page'})));
  const tools=el('div',{className:'tools'},q,size);
  const scroll=el('div',{className:'scroll'});
  const prev=el('button',{className:'secondary',textContent:'Previous'}),next=el('button',{className:'secondary',textContent:'Next'});
  const info=el('span',{className:'grow'});
  const pager=el('div',{className:'pager'},info,prev,next);
  root.replaceChildren(h2,tools,scroll,pager);
  const text=(r,c)=>{const v=r[c.key];return v==null?(c.empty||'Not Available'):String(c.fmt?c.fmt(v,r):v)};
  function draw(){
    const needle=st.q.toLowerCase();
    let rows=st.rows.filter(r=>!needle||cols.some(c=>text(r,c).toLowerCase().includes(needle)));
    if(st.sort){const c=cols.find(c=>c.key===st.sort);rows=rows.slice().sort((a,b)=>{const x=a[c.key],y=b[c.key];if(x==null)return 1;if(y==null)return -1;return (typeof x==='number'&&typeof y==='number'?x-y:String(x).localeCompare(String(y)))*st.dir})}
    const pages=Math.max(1,Math.ceil(rows.length/st.size));st.page=Math.min(st.page,pages-1);
    const view=rows.slice(st.page*st.size,(st.page+1)*st.size);
    const tr=el('tr');
    cols.forEach(c=>{const arrow=st.sort===c.key?(st.dir>0?' \u25B2':' \u25BC'):'';
      const th=el('th',{textContent:c.label+arrow,title:c.tip+' Click to sort.',className:c.num?'num':''});
      th.onclick=()=>{if(st.sort===c.key)st.dir=-st.dir;else{st.sort=c.key;st.dir=1}draw()};tr.append(th)});
    const body=el('tbody');
    view.forEach(r=>body.append(el('tr',{},...cols.map(c=>el('td',{textContent:text(r,c),className:c.num?'num':''})))));
    const table=el('table',{},el('thead',{},tr),body);
    // The column headers always stay. With no rows the note goes in the body of the table, never in place of it.
    if(!rows.length)body.append(el('tr',{className:'empty-row'},el('td',{colSpan:cols.length,className:'empty',textContent:st.rows.length?'No rows match the filter.':emptyText})));
    scroll.replaceChildren(table);
    info.textContent=rows.length?`Showing ${st.page*st.size+1} to ${st.page*st.size+view.length} of ${rows.length}`:'No rows';
    prev.disabled=st.page<=0;prev.title=prev.disabled?'You are on the first page.':'Go to the previous page.';
    next.disabled=st.page>=pages-1;next.title=next.disabled?'You are on the last page.':'Go to the next page.';
  }
  q.oninput=()=>{st.q=q.value;st.page=0;draw()};size.onchange=()=>{st.size=+size.value;st.page=0;draw()};
  prev.onclick=()=>{st.page--;draw()};next.onclick=()=>{st.page++;draw()};
  return {set(rows){st.rows=rows;draw()}};
}
