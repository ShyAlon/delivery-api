"""Actual JS behavior guards for the approved stale wiring-check updates."""
import subprocess
from pathlib import Path
import pytest

WEB=Path(__file__).parents[1]/'src/delivery_api/web'


def run(tmp_path,source):
    file=tmp_path/'probe.mjs';file.write_text(source)
    result=subprocess.run(['node',str(file)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.parametrize('path',['/','/tasks/t/view'])
def test_disposed_balance_never_starts_another_read_or_publishes_late_result(tmp_path,path):
    source='\n'.join(l for l in (WEB/'session-cost.js').read_text().splitlines() if not l.startswith('import '))
    run(tmp_path,'''
let resolveBalance,reads=0;const timers=[],listeners={};
const api=async url=>{if(url==='/api/account/credits'){reads++;return await new Promise(r=>resolveBalance=r)}return {price:1,price_usd:1,phases:[]}};
const noteActivity=()=>{};const balanceLabel=r=>String(r.credits),balanceTitle=()=>'',costTitle=()=>'';
const host={dataset:{},closest:()=>({getBoundingClientRect:()=>({height:60})})};
const balance={dataset:{},textContent:'sentinel',hidden:false};
globalThis.location={pathname:PATH};
globalThis.document={hidden:false,querySelector:s=>s==='#sessioncost'?host:balance,
 documentElement:{style:{setProperty(){}}},addEventListener:(n,f)=>listeners[n]=f,removeEventListener(){}};
globalThis.window={addEventListener(){},removeEventListener(){}};
globalThis.ResizeObserver=class{observe(){}disconnect(){}};
globalThis.setInterval=f=>{timers.push(f);return 1};globalThis.clearInterval=()=>{};
'''.replace('PATH',repr(path))+source+'''
const dispose=startSessionCostHeader();dispose();
resolveBalance({credits:99});await new Promise(r=>setImmediate(r));
if(balance.textContent!=='sentinel')throw Error('disposed balance published a late result');
listeners.visibilitychange();timers[0]();await new Promise(r=>setImmediate(r));
if(reads!==1)throw Error('disposed balance started another read');
''')


def test_debug_collector_keeps_history_payload_and_unavailability(tmp_path):
    run(tmp_path,(WEB/'debug-package.js').read_text()+'''
const calls=[];const history={text:'credit history evidence'};
const api=async url=>{calls.push(url);return {sections:[{source:'credit-history',availability:'available',data:history}]}};
let evidence=await collectDebugEvidence({taskId:null,operator:false,cloud:true,api});
let row=evidence.sections.find(x=>x.source==='credit-history');
if(row?.availability!=='available'||row.data!==history)throw Error('credit history not collected');
if(calls.length!==1||calls[0]!=='/api/debug/collection')throw Error('collection must be one fixed request');
evidence=await collectDebugEvidence({taskId:null,operator:false,cloud:true,api:async()=>({sections:[{source:'credit-history',availability:'unavailable',category:'read_failed'}]})});
row=evidence.sections.find(x=>x.source==='credit-history');
if(row?.availability!=='unavailable'||row.category!=='read_failed')throw Error('failed history presented as available');
''')
