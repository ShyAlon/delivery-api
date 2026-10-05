import {esc} from './client-core.js';
export const HUMAN_LABELS={"interviewee.answer":"Interviewee Answer","gate.auto_decision":"Gate Auto Decision","gate.simulated_expert_opinion":"Gate Simulated Expert Opinion","not_started":"Not Started","in_progress":"In Progress","intent":"Start","intent_model":"Start","conops":"Concept of Operations","needs":"Stakeholder Needs","srs":"Requirements","architecture":"Architecture","specs":"Specifications","tests":"Tests","validation":"Validation"};
export function humanLabel(value){if(value==null)return '';let key=String(value);if(HUMAN_LABELS[key])return HUMAN_LABELS[key];return key.replace(/[._-]+/g,' ').replace(/([a-z0-9])([A-Z])/g,'$1 $2').replace(/\b\w/g,c=>c.toUpperCase())}
export function markdownInline(text){return esc(text).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>')}
export function markdownHtml(text){
 const lines=String(text||'').split('\n'),html=[];let list=null,code=null,table=null;
 const closeList=()=>{if(list){html.push(`</${list}>`);list=null}},closeTable=()=>{if(table){html.push('</tbody></table>');table=null}};
 const cells=l=>l.trim().replace(/^\||\|$/g,'').split('|').map(c=>c.trim());
 for(let n=0;n<lines.length;n++){const line=lines[n];
  if(code!==null){if(/^\s*```/.test(line)){html.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`);code=null}else code.push(line);continue}
  if(/^\s*```/.test(line)){closeList();closeTable();code=[];continue}
  if(/^\s*\|.*\|\s*$/.test(line)){const next=lines[n+1]||'';if(!table&&/^\s*\|?\s*:?-{2,}/.test(next)){closeList();table=true;html.push('<table><thead><tr>'+cells(line).map(c=>`<th>${markdownInline(c)}</th>`).join('')+'</tr></thead><tbody>');n++;continue}if(table){html.push('<tr>'+cells(line).map(c=>`<td>${markdownInline(c)}</td>`).join('')+'</tr>');continue}}
  closeTable();
  const heading=line.match(/^(#{1,6}) (.*)$/);
  if(heading){closeList();html.push(`<h${heading[1].length}>${markdownInline(heading[2])}</h${heading[1].length}>`);continue}
  const bullet=line.match(/^\s*[-*] (.*)$/),number=line.match(/^\s*\d+[.)] (.*)$/);
  if(bullet||number){const tag=bullet?'ul':'ol';if(list!==tag){closeList();html.push(`<${tag}>`);list=tag}html.push(`<li>${markdownInline((bullet||number)[1])}</li>`);continue}
  if(!line.trim()){closeList();continue}
  closeList();html.push(`<p>${markdownInline(line)}</p>`)}
 if(code!==null)html.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`);closeList();closeTable();return html.join('')}

export const QA_MAX_WORDS=32;
export function truncateWords(text,max=QA_MAX_WORDS){const t=String(text??'').trim(),w=t.split(/\s+/);return w.length>max?w.slice(0,max).join(' ')+'…':t}

const SLOT_ACRONYMS=new Set(['ui','ux','pwa','api','db','sql','css','html','json','url','id','sdk','ci','cd','os','pdf','http','https','tts','midi','ml','llm','ai','cli','gui','io','jwt','oauth','sso','cdn','dns','tls','ssl','spa','ssr','orm']);
export function humanSlotKey(key){return String(key).split(/[_-]+/).filter(Boolean).map(w=>SLOT_ACRONYMS.has(w.toLowerCase())?w.toUpperCase():w.charAt(0).toUpperCase()+w.slice(1)).join(' ')}
// Display-level title casing: any snake_case token in a rendered title or progress line becomes Title Case ("stack:ui_framework" -> "stack:UI Framework"). Text without such tokens is untouched.
export function humanizeSnakeCase(text){return String(text??'').replace(/\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g,humanSlotKey)}
