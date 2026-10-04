import {esc} from './client-core.js';
export const HUMAN_LABELS={"interviewee.answer":"Interviewee Answer","gate.auto_decision":"Gate Auto Decision","gate.simulated_expert_opinion":"Gate Simulated Expert Opinion","not_started":"Not Started","in_progress":"In Progress","intent":"Start","intent_model":"Start","conops":"Concept of Operations","needs":"Stakeholder Needs","srs":"Requirements","architecture":"Architecture","specs":"Specifications","tests":"Tests"};
export function humanLabel(value){if(value==null)return '';let key=String(value);if(HUMAN_LABELS[key])return HUMAN_LABELS[key];return key.replace(/[._-]+/g,' ').replace(/([a-z0-9])([A-Z])/g,'$1 $2').replace(/\b\w/g,c=>c.toUpperCase())}
export function markdownInline(text){return esc(text).replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>')}
export function markdownHtml(text){let lines=String(text||'').split('\n'),html=[],inList=false;for(let line of lines){if(/^#{1,4} /.test(line)){if(inList){html.push('</ul>');inList=false}let level=line.match(/^#+/)[0].length;html.push(`<h${level}>${markdownInline(line.slice(level+1))}</h${level}>`)}else if(/^[-*] /.test(line)){if(!inList){html.push('<ul>');inList=true}html.push(`<li>${markdownInline(line.slice(2))}</li>`)}else if(!line.trim()){if(inList){html.push('</ul>');inList=false}}else{if(inList){html.push('</ul>');inList=false}html.push(`<p>${markdownInline(line)}</p>`)}}if(inList)html.push('</ul>');return html.join('')}

export const QA_MAX_WORDS=32;
export function truncateWords(text,max=QA_MAX_WORDS){const t=String(text??'').trim(),w=t.split(/\s+/);return w.length>max?w.slice(0,max).join(' ')+'…':t}

const SLOT_ACRONYMS=new Set(['ui','ux','pwa','api','db','sql','css','html','json','url','id','sdk','ci','cd','os','pdf','http','https','tts','midi','ml','llm','ai','cli','gui','io','jwt','oauth','sso','cdn','dns','tls','ssl','spa','ssr','orm']);
export function humanSlotKey(key){return String(key).split(/[_-]+/).filter(Boolean).map(w=>SLOT_ACRONYMS.has(w.toLowerCase())?w.toUpperCase():w.charAt(0).toUpperCase()+w.slice(1)).join(' ')}
// Display-level title casing: any snake_case token in a rendered title or progress line becomes Title Case ("stack:ui_framework" -> "stack:UI Framework"). Text without such tokens is untouched.
export function humanizeSnakeCase(text){return String(text??'').replace(/\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g,humanSlotKey)}
