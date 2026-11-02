"""Bounded client export concurrency without extra authority or dropped sections."""
from pathlib import Path
import subprocess
ROOT=Path(__file__).parents[1]
def test_groups_overlap_at_two_preserve_order_errors_and_timings():
    script=r'''
import {collectExportGroups} from './src/delivery_api/web/debug-package.js';
let active=0,peak=0;
const make=(id,bad=false)=>async()=>{active++;peak=Math.max(peak,active);await new Promise(r=>setTimeout(r,30));active--;if(bad)throw Error('unavailable');return id;};
const result=await collectExportGroups([make('a'),make('b',true),make('c'),make('d')]);
if(peak!==2||active!==0||result[0].value!=='a'||!result[1].error||result[2].value!=='c'||result[3].value!=='d'||result.some(x=>x.duration_ms<0))throw Error(JSON.stringify({peak,result}));
'''
    result=subprocess.run(['node','--input-type=module','-e',script],cwd=ROOT,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
