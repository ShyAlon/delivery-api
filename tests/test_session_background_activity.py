"""Exercise actual header callbacks without waiting a minute or expiring auth."""
import json
from pathlib import Path
import subprocess


def test_empty_route_background_refresh_is_not_user_activity(tmp_path):
    source=(Path(__file__).parents[1]/'src/delivery_api/web/session-cost.js').read_text()
    source='\n'.join(line for line in source.splitlines() if not line.startswith('import '))
    probe=tmp_path/'header.mjs'
    probe.write_text('''
let activity=0, reads=0;const timers=[],listeners={};
const api=async()=>{reads++;return {credits:0}};
const noteActivity=()=>{activity++};
const balanceLabel=()=>null,balanceTitle=()=>'',costTitle=()=>'';
const header={getBoundingClientRect:()=>({height:60})};
const host={dataset:{},closest:()=>header};const balance={dataset:{}};
globalThis.location={pathname:'/'};
globalThis.document={hidden:false,querySelector:s=>s==='#sessioncost'?host:balance,
 documentElement:{style:{setProperty(){}}},addEventListener:(n,f)=>{listeners[n]=f},removeEventListener(){}};
globalThis.window={addEventListener(){},removeEventListener(){}};
globalThis.ResizeObserver=class{observe(){}disconnect(){}};
globalThis.setInterval=(f,ms)=>{timers.push({f,ms});return 1};globalThis.clearInterval=()=>{};
''' + source + '''
const dispose=startSessionCostHeader();await Promise.resolve();await Promise.resolve();
const before=reads;timers[0].f();await Promise.resolve();await Promise.resolve();
if(activity!==0)throw Error('background timer marked user activity');
if(reads<=before)throw Error('background timer stopped balance refresh');
if(timers[0].ms!==60000)throw Error('safety cadence changed');
listeners.visibilitychange();await Promise.resolve();
if(activity!==1)throw Error('visible resume did not mark activity');
document.hidden=true;timers[0].f();listeners.visibilitychange();
if(activity!==1)throw Error('hidden page marked activity');
dispose();console.log(JSON.stringify({activity,cadence:timers[0].ms}));
''')
    result=subprocess.run(['node',str(probe)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)=={'activity':1,'cadence':60000}
