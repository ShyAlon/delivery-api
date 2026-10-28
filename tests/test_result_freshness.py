import subprocess


def test_freshness_preserves_content_marks_failure_and_recovers():
    code="""
    import {refreshResult} from './src/delivery_api/web/result-freshness.js';
    let notice=null;const slot={isConnected:true,dataset:{},before:n=>notice=n};
    globalThis.document={createElement:()=>({className:'',textContent:'',append(){},remove(){notice=null}})};
    await refreshResult(slot,async()=> 'ok');
    const at=slot.dataset.lastSuccess;
    try{await refreshResult(slot,async()=>{throw Error('private')})}catch{}
    if(!notice.textContent.includes('stale')||!notice.textContent.includes(at))throw Error('missing stale');
    await refreshResult(slot,async()=> 'ok');
    if(notice!==null)throw Error('notice did not recover');
    """
    r=subprocess.run(['node','--input-type=module','-e',code],capture_output=True,text=True)
    assert r.returncode==0,r.stderr
