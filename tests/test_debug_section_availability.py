import subprocess,json


def test_section_status_distinguishes_empty_failed_and_policy_excluded():
    code="""
    import {readSection} from './src/delivery_api/web/debug-sections.js';
    const ok=await readSection('events',()=>Promise.resolve([]));
    const fail=await readSection('events',()=>Promise.reject(new Error('private-payload')));
    const excluded=await readSection('cloud',()=>{throw new Error('must not call')},true);
    console.log(JSON.stringify({ok,fail,excluded}));
    """
    r=subprocess.run(['node','--input-type=module','-e',code],capture_output=True,text=True)
    assert r.returncode==0,r.stderr
    d=json.loads(r.stdout)
    assert d['ok']['availability']=='available' and d['ok']['data']==[] and d['ok']['last_success_at']
    assert d['fail']['availability']=='unavailable' and d['fail']['category']=='read_failed'
    assert d['fail']['last_success_at']==d['ok']['last_success_at']
    assert 'private-payload' not in r.stdout
    assert d['excluded']['availability']=='policy_excluded'


def test_export_uses_one_fixed_server_collection_request():
    code="""
    import {collectDebugEvidence} from './src/delivery_api/web/debug-package.js';
    const calls=[];const result=await collectDebugEvidence({taskId:'fixture',operator:true,cloud:true,api:async url=>{calls.push(url);return {sections:[{source:'credits',availability:'available',duration_ms:2}],scope:'bounded'};}});
    console.log(JSON.stringify({result,calls}));
    """
    r=subprocess.run(['node','--input-type=module','-e',code],capture_output=True,text=True)
    assert r.returncode==0,r.stderr
    d=json.loads(r.stdout);assert d['calls']==['/api/debug/collection?task_id=fixture']
    assert d['result']['sections'][0]['duration_ms']==2 and d['result']['duration_ms']>=0
