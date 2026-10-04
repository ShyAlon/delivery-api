import {createDecisions} from './decisions.js';
import {createQa} from './qa.js';
import {createIdentity} from './identity.js';
import {createDialogs} from './dialogs.js';
import {createDashboard} from './dashboard.js';
import {createTask} from './task.js';
import {createGate} from './gate.js';
import {createCompletion} from './completion.js';
import {createTaskActions} from './taskactions.js';
import {createSessions} from './sessions.js';
import {clientLog,esc} from './client-core.js';

window.addEventListener('click',e=>{let target=e.target.closest('a,button,[role="button"]');if(target)clientLog('click',target.getAttribute('aria-label')||target.textContent.trim().replace(/\s+/g,' ').slice(0,120),{tag:target.tagName.toLowerCase()})},true);
window.addEventListener('error',e=>ctx.debugState.client_errors.push({ts:new Date().toISOString(),message:e.message,source:e.filename,line:e.lineno}));window.addEventListener('unhandledrejection',e=>ctx.debugState.client_errors.push({ts:new Date().toISOString(),message:String(e.reason?.message||e.reason)}));
window.addEventListener('popstate',()=>clientLog('navigation','Browser history navigation',{url:location.href}));
clientLog('navigation','Page loaded',{url:location.href});

const ctx={};
for(const create of [createQa,createIdentity,createDialogs,createDashboard,createTask,createGate,createCompletion,createDecisions,createTaskActions,createSessions])Object.assign(ctx,create(ctx));
const {loadIdentity,decisionPage,gatePage,completePage,bundleView,sessionsDashboard,aboutPage,detail,dashboard,showError}=ctx;
loadIdentity().then(ok=>{if(!ok)return;const decisionMatch=location.pathname.match(/^\/tasks\/([^/]+)\/decisions\/([^/]+)$/),gateMatch=location.pathname.match(/^\/tasks\/([^/]+)\/gates\/([^/]+)$/),completeMatch=location.pathname.match(/^\/tasks\/([^/]+)\/complete$/),taskMatch=location.pathname.match(/^\/tasks\/([^/]+)\/view/),sessionMatch=location.pathname.match(/^\/sessions\/([^/]+)(?:\/(interview|needs|srs|architecture|specs|tests|reviews|oracle)|\/view)?$/);decisionMatch?decisionPage(decodeURIComponent(decisionMatch[1]),decodeURIComponent(decisionMatch[2])).catch(showError):gateMatch?gatePage(decodeURIComponent(gateMatch[1]),decodeURIComponent(gateMatch[2])).catch(showError):completeMatch?completePage(decodeURIComponent(completeMatch[1])).catch(showError):sessionMatch?bundleView(sessionMatch[1],sessionMatch[2]||'interview').catch(showError):location.pathname==='/sessions'?sessionsDashboard().catch(showError):location.pathname==='/about'?aboutPage():taskMatch?detail(taskMatch[1]).catch(showError):dashboard().catch(showError)}).catch(showError);
window.addEventListener('unhandledrejection',e=>showError(e.reason));
