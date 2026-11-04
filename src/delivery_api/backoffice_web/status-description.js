// Private endpoint labels stay out of customer assets. Lifecycle/view are shared.
import {requestDescription as sharedDescription} from '/assets/operation-status.js';
export function requestDescription(url,options={}){
 const path=new URL(url,location.origin).pathname;
 const job=/\/worker-jobs\/([^/]+)\/stop$/.exec(path);
 if(job){let hide=false;try{hide=JSON.parse(options.body||'{}').hide===true}catch{}return (hide?'Deleting task':'Stopping task')+' (job '+decodeURIComponent(job[1]).slice(0,80)+')';}
 const name=path.split('/').at(-1),labels={'access':'Checking service permissions','active-jobs':'Loading worker jobs','build':'Checking running build','workspace':'Loading workspace','orphaned-tasks':'Loading orphan review','operator-credits':'Loading credit accounts','operator-sessions':'Loading sessions','expense-totals':'Loading provider expense','vendor-status':'Loading vendor terms','payment-journal':'Loading purchase journal','payment-totals':'Loading payment totals','task-debug':'Loading task details','task-logs':'Loading task events','task-usage':'Loading task usage','credit-account':'Loading credit account','attempt-releases':'Loading reservation releases','model-pins':'Loading model pins','acceptance-readiness':'Checking acceptance readiness','audit-events':'Loading audit events'};
 if(labels[name])return labels[name];
 if(path.includes('/model-pins/'))return ({new:'Reviewing new model','new-local':'Reviewing local model',refresh:'Fetching vendor prices',edit:'Reviewing model changes',approve:'Saving reviewed model','expense-policy':'Saving expense policy'})[name]||'Reviewing model pin';
 if(path.endsWith('/resolve'))return 'Resolving audit event';
 return sharedDescription(url,options);
}
