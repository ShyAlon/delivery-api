"""Synchronous request stays live after authoritative UI handoff. No model/network."""
import subprocess
from pathlib import Path

def test_launch_handoff_waits_for_new_command_not_running_claim_and_never_resends():
    module=(Path(__file__).parents[1]/'src/delivery_api/web/launch-handoff.js').as_uri()
    script='''
const {launchHandoff}=await import(MODULE);
let sendCount=0,reads=0,handed=0,resolve,reject;
const pending=new Promise((a,b)=>{resolve=a;reject=b});
const running=launchHandoff({send:()=>{sendCount++;return pending},readTask:async()=>{reads++;return {state:'running',events:[{id:reads<3?10:11,kind:'command',phase_key:'architecture'}]}},phase:'architecture',afterId:10,interval:1,onLaunched:()=>{handed++}});
await new Promise(r=>setTimeout(r,25));
if(sendCount!==1||handed!==1||reads<3)throw Error('incorrect handoff '+[sendCount,handed,reads]);
resolve({done:true});await running;if(handed!==1)throw Error('duplicate handoff');
let failed=false;
try{await launchHandoff({send:()=>Promise.reject(Error('refused')),readTask:()=>{throw Error('must not poll')},phase:'architecture',afterId:10,interval:1,onLaunched:()=>{throw Error('false success')}})}catch(e){failed=e.message==='refused'}
if(!failed)throw Error('lost refusal');
let lateReject,lateHandoff=0;
const late=launchHandoff({send:()=>new Promise((a,b)=>{lateReject=b}),readTask:async()=>({events:[{id:12,kind:'command',phase_key:'architecture'}]}),phase:'architecture',afterId:10,interval:1,onLaunched:()=>{lateHandoff++}});
await new Promise(r=>setTimeout(r,10));lateReject(Error('late failure'));
try{await late;throw Error('lost late failure')}catch(e){if(e.message!=='late failure'||lateHandoff!==1)throw e}
'''.replace('MODULE',repr(module))
    subprocess.run(['node','--input-type=module','-e',script],check=True)
