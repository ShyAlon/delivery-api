import {activationAllowed} from './pointer-activation.js';
// One Table owns table markup, cell behavior and structural styles in both apps.
const styles=`
.ui-table{border-collapse:collapse;width:100%;font-size:13px;text-align:left}
.ui-table th,.ui-table td{padding:11px 10px;border-bottom:1px solid var(--line,#d7ddd4);vertical-align:top;overflow-wrap:anywhere}
.ui-table thead{position:sticky;top:0;z-index:2;background:var(--card,#fff)}
.ui-table th{position:sticky;top:0;background:var(--card,#fff);font:11px DM Mono,monospace;color:var(--muted,#667066);z-index:1}
.ui-table th button{padding:0;background:transparent;min-height:24px;color:inherit}
.ui-table .num{text-align:right}.ui-table .detail{background:var(--paper,#edf0e8)}
.ui-table-sort{margin:0 0 12px 12px;max-width:100%}
.ui-table-tools,.ui-table-pager{display:flex;gap:8px;align-items:center;padding:8px 12px}.ui-table-pager .grow{flex:1}
.ui-table-scroll{overflow-x:auto;overflow-y:visible;max-height:none}.ui-table-scroll:has(.detail){max-height:none}
.audit-table-wrap{max-height:none;overflow-x:auto;overflow-y:visible;border:1px solid var(--line,#d7ddd4);border-radius:14px;background:var(--card,#fff)}
.ui-table.ui-table-content{margin:8px 0}.ui-table-content th,.ui-table-content td{border:1px solid var(--line,#d7ddd4);padding:6px 8px}
@media(max-width:650px){.ui-table.ui-table-responsive,.ui-table-responsive tbody,.ui-table-responsive tr,.ui-table-responsive td{display:block;width:100%}.ui-table-responsive thead{display:none}.ui-table-responsive td{border:0;padding:6px 0}.ui-table-responsive td:before{content:attr(data-label) ': ';font-weight:700}.ui-table-responsive tr{border-bottom:1px solid var(--line,#d7ddd4);padding:10px 0}.ui-table-responsive td.detail-cell:before{content:none}}
`;
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const el=(tag,props,...kids)=>{const n=document.createElement(tag);Object.assign(n,props||{});kids.forEach(k=>n.append(k));return n};
export class Table{
 static interactive(root,title,tip,cols,emptyText){
  const st={rows:[],sort:null,dir:1,page:0,size:10,q:''};
  const h2=el('h2',{textContent:title,title:tip});
  const q=el('input',{type:'search',placeholder:'Filter',title:'Show only rows that contain this text in any column.'});
  const size=el('select',{title:'How many rows to show per page.'});
  [10,25,50].forEach(n=>size.append(el('option',{value:n,textContent:n+' Per Page'})));
  const tools=el('div',{className:'ui-table-tools'},q,size);
  const scroll=el('div',{className:'ui-table-scroll'});
  const prev=el('button',{className:'secondary',textContent:'Previous'}),next=el('button',{className:'secondary',textContent:'Next'});
  const info=el('span',{className:'grow'});
  const pager=el('div',{className:'ui-table-pager'},info,prev,next);
  root.replaceChildren(h2,tools,scroll,pager);
  const text=(r,c)=>{const v=r[c.key];return v==null?(c.empty||'Not Available'):String(c.fmt?c.fmt(v,r):v)};
  function draw(){
    const needle=st.q.toLowerCase();
    let rows=st.rows.filter(r=>!needle||cols.some(c=>text(r,c).toLowerCase().includes(needle)));
    if(st.sort){const c=cols.find(c=>c.key===st.sort);rows=rows.slice().sort((a,b)=>{const x=a[c.key],y=b[c.key];if(x==null)return 1;if(y==null)return -1;return (typeof x==='number'&&typeof y==='number'?x-y:String(x).localeCompare(String(y)))*st.dir})}
    const pages=Math.max(1,Math.ceil(rows.length/st.size));st.page=Math.min(st.page,pages-1);
    const view=rows.slice(st.page*st.size,(st.page+1)*st.size);
    const component=new Table(scroll,{columns:cols.map(c=>c.label),responsive:false}),tr=component.node.tHead.rows[0];
    cols.forEach((c,i)=>{const arrow=st.sort===c.key?(st.dir>0?' \u25B2':' \u25BC'):'';
      const th=tr.children[i];th.textContent=c.label+arrow;th.title=(c.tip||'')+' Click to sort.';th.className=c.num?'num':'';
      th.onclick=e=>{if(!activationAllowed(e))return;if(st.sort===c.key)st.dir=-st.dir;else{st.sort=c.key;st.dir=1}draw()}});
    const body=component.body;
    view.forEach(r=>{const row=Table.row({querySelectorAll:()=>tr.children},cols.map(c=>text(r,c)));cols.forEach((c,i)=>row.children[i].classList.toggle('num',!!c.num));body.append(row)});
    const table=component.node;
    // The column headers always stay. With no rows the note goes in the body of the table, never in place of it.
    if(!rows.length)component.empty(st.rows.length?'No rows match the filter.':emptyText);
    scroll.replaceChildren(table);
    info.textContent=rows.length?`Showing ${st.page*st.size+1} to ${st.page*st.size+view.length} of ${rows.length}`:'No rows';
    prev.disabled=st.page<=0;prev.title=prev.disabled?'You are on the first page.':'Go to the previous page.';
    next.disabled=st.page>=pages-1;next.title=next.disabled?'You are on the last page.':'Go to the next page.';
  }
  q.oninput=()=>{st.q=q.value;st.page=0;draw()};size.onchange=()=>{st.size=+size.value;st.page=0;draw()};
  prev.onclick=()=>{st.page--;draw()};next.onclick=()=>{st.page++;draw()};
  return {set(rows){st.rows=rows;draw()}};

 }
 static styles(){if(typeof document!=='undefined'&&!document.getElementById('ui-table-styles')){const s=document.createElement('style');s.id='ui-table-styles';s.textContent=styles;document.head.append(s)}}
 static html(columns,rows,{className='ui-table-content'}={}){Table.styles();return `<table class="ui-table ${escape(className)}"><thead><tr>${columns.map(c=>`<th>${c}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map((cell,i)=>`<td data-label="${escape(columns[i]?.replace(/<[^>]*>/g,''))}">${cell}</td>`).join('')}</tr>`).join('')}</tbody></table>`}
 constructor(host,{id,columns=[],hidden=false,responsive=true,bodyId}={}){
  Table.styles();this.columns=columns;this.node=document.createElement('table');this.node.className='ui-table'+(responsive?' ui-table-responsive':'');if(id)this.node.id=id;this.node.hidden=hidden;
  const head=this.node.createTHead(),row=head.insertRow();for(const col of columns){const th=document.createElement('th');if(typeof col==='string')th.textContent=col;else if(col.button){const b=document.createElement('button');b.textContent=col.label;for(const [key,value] of Object.entries(col.button))b.setAttribute(key,value);th.append(b)}else th.textContent=col.label;row.append(th)}
  this.body=this.node.createTBody();if(bodyId)this.body.id=bodyId;host.replaceChildren(this.node);
 }
 static cell(table,row,value){const cell=document.createElement('td');cell.dataset.label=table.querySelectorAll('th')[row.children.length]?.textContent||'';if(value&&typeof value==='object'&&value.nodeType)cell.append(value);else cell.textContent=String(value??'Unavailable');row.append(cell);return cell}
 static rows(rows,{query='',search=row=>JSON.stringify(row),sort=null,value=(row,key)=>row[key],descending=false,compare=null}={}){
  let shown=rows.filter(row=>String(search(row)).toLowerCase().includes(String(query).toLowerCase()));
  if(sort!==null)shown=shown.slice().sort((a,b)=>(compare?compare(value(a,sort),value(b,sort)):String(value(a,sort)??'').localeCompare(String(value(b,sort)??''),undefined,{numeric:true}))*(descending?-1:1));
  return shown;
 }
 static sortControl(table,filter,onSort){const select=el('select');select.setAttribute('aria-label','Sort this page');select.className='ui-table-sort';select.add(new Option('Sort this page',''));table.querySelectorAll('[data-sort]').forEach(button=>{select.add(new Option(button.textContent,button.dataset.sort));button.addEventListener('click',()=>onSort(button.dataset.sort,true));});select.addEventListener('change',()=>onSort(select.value,false));filter.after(select);return select}
 static detail(row,message){const detail=document.createElement('tr');detail.className='detail';const cell=document.createElement('td');cell.className='detail-cell';cell.colSpan=row.children.length;cell.textContent=message;detail.append(cell);row.after(detail);return {detail,cell}}
 static row(table,values){const row=document.createElement('tr');values.forEach(value=>Table.cell(table,row,value));return row}
 set(rows,values=row=>row){this.body.replaceChildren(...rows.map(row=>Table.row(this.node,values(row))));return this}
 empty(message){const row=this.body.insertRow(),cell=row.insertCell();cell.colSpan=this.columns.length;cell.textContent=message;return this}
 static mountSlots(root,schemas){for(const slot of root.querySelectorAll('[data-ui-table]')){const schema=schemas[slot.dataset.uiTable];if(!schema)throw Error('Missing Table schema '+slot.dataset.uiTable);const component=new Table(slot,schema);slot.replaceWith(component.node)}}
}
