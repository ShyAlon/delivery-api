/* Structured review values shared by private review dialogs. No HTML from data. */
const names={provider:'Provider',model:'Model',price_version:'Price review',source:'Source',expense_input:'Input price',expense_cache_hit:'Cached input price',expense_cache_miss:'Uncached input price',expense_output:'Output price',expense_units_per_usd:'Price unit scale',retail_input:'Customer input rate',retail_output:'Customer output rate',retail_units_per_credit:'Credit unit scale',context_tokens:'Context window',maximum_output_tokens:'Maximum output',bound_version:'Bounds review',protective_run_ceiling:'Conservative request bound',fetched_at:'Prices checked',model_version:'Model version',raw_context_limit:'Published context window',raw_output_limit:'Published output limit'};
const words=value=>value.replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());
export function usdPerMillion(value,scale){
 try{let n=BigInt(value)*1000000n,d=BigInt(scale);if(d<=0n)throw Error();const whole=n/d,frac=(n%d*100000000n/d).toString().padStart(8,'0').replace(/0+$/,'');return '$'+whole+(frac?'.'+frac:'')+' USD per million tokens'}catch{return 'Invalid price - review required'}
}
function display(value,key,root){
 if(value===undefined)return 'Not set';if(value===null)return 'None';
 if(key.startsWith('expense_')&&key!=='expense_units_per_usd')return usdPerMillion(value,root.row?.expense_units_per_usd??1000000000000);
 if(key==='price_version'||key==='bound_version'||key==='fetched_at'){
  const match=String(value).match(/\d{4}-\d\d-\d\dT.+/);if(match){const date=new Date(match[0]);if(!Number.isNaN(date.valueOf()))return new Intl.DateTimeFormat('en-GB',{dateStyle:'medium',timeStyle:'medium',timeZone:'UTC'}).format(date)+' UTC'}
 }
 if(typeof value==='boolean')return value?'Yes':'No';if(typeof value==='number')return value.toLocaleString('en-US');
 return Array.isArray(value)?value.map(v=>typeof v==='object'?JSON.stringify(v):v).join(', '):String(value);
}
function flatten(value,path=[],out=[]){for(const [key,item] of Object.entries(value||{})){const next=[...path,key];if(item&&typeof item==='object'&&!Array.isArray(item))flatten(item,next,out);else out.push({path:next,value:item})}return out}
export function reviewDiff(before,after){
 const box=document.createElement('div');box.className='review-diff';const old=new Map(flatten(before).map(x=>[x.path.join('.'),x.value]));
 const rows=flatten(after).filter(x=>JSON.stringify(old.get(x.path.join('.')))!==JSON.stringify(x.value));
 if(!rows.length){box.textContent='No price or bounds changed. Approving makes this the provider default.';return box}
 const list=document.createElement('dl'),extra=document.createElement('dl');for(const {path,value} of rows){const key=path.at(-1),term=document.createElement('dt'),description=document.createElement('dd');
 term.textContent=names[key]||path.filter(k=>!['row','context','provider_snapshot'].includes(k)).map(words).join(' / ');
 const previous=old.get(path.join('.'));if(previous!==undefined){const from=document.createElement('del');from.textContent=display(previous,key,before);description.append(from,document.createElement('br'))}
 const to=document.createElement('span');to.textContent=display(value,key,after);description.append(to);const primary=(path.length===2&&path[0]==='row'&&['provider','model','price_version','expense_input','expense_cache_hit','expense_cache_miss','expense_output'].includes(key))||(path[0]==='context'&&['context_tokens','maximum_output_tokens'].includes(key));(primary?list:extra).append(term,description)}box.append(list);if(extra.children.length){const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent='Source and calculation details';details.append(summary,extra);box.append(details)}return box;
}
