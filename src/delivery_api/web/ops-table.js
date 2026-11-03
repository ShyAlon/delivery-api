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
    view.forEach(r=>{const row=tableRow({querySelectorAll:()=>tr.children},cols.map(c=>text(r,c)));cols.forEach((c,i)=>row.children[i].classList.toggle('num',!!c.num));body.append(row)});
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

export function tableCell(table,row,value){const cell=document.createElement('td');cell.dataset.label=table.querySelectorAll('th')[row.children.length]?.textContent||'';if(value&&typeof value==='object'&&value.nodeType)cell.append(value);else cell.textContent=String(value??'Unavailable');row.append(cell);return cell;}
export function tableRow(table,values){const row=document.createElement('tr');values.forEach(value=>tableCell(table,row,value));return row;}
export function detailTable(host,labels,items,values){const table=el('table'),head=el('thead'),tr=el('tr'),body=el('tbody');labels.forEach(label=>tr.append(el('th',{textContent:label})));head.append(tr);table.append(head,body);items.forEach(item=>body.append(tableRow(table,values(item))));host.append(table);return table;}
export function fieldList(host,fields){const list=el('dl',{className:'detail-fields'});for(const [label,value] of fields)list.append(el('dt',{textContent:label}),el('dd',{textContent:String(value??'Unavailable')}));host.append(list);return list;}

export function mountSort(table,filter,onSort){const select=el('select');select.setAttribute('aria-label','Sort this page');select.className='table-sort';select.add(new Option('Sort this page',''));table.querySelectorAll('[data-sort]').forEach(button=>{select.add(new Option(button.textContent,button.dataset.sort));button.addEventListener('click',()=>onSort(button.dataset.sort,true));});select.addEventListener('change',()=>onSort(select.value,false));filter.after(select);return select;}
