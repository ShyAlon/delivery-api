import subprocess


def test_failed_scheduled_renewal_dispatches_once_without_losing_drafts():
    code="""
    globalThis.document={addEventListener(){}};let events=[];
    globalThis.window={addEventListener(){},dispatchEvent:e=>events.push(e)};
    globalThis.CustomEvent=class{constructor(type,opts){this.type=type;this.detail=opts.detail}};
    globalThis.fetch=async()=>({ok:false});
    const m=await import('./src/delivery_api/web/client-core.js');
    m.setSessionExpiry(1);
    try{await m.renewSessionIfDue()}catch{}
    await m.renewSessionIfDue();
    if(events.filter(e=>e.type==='requi:signin-needed').length!==1)throw Error('missing expiry state');
    """
    r=subprocess.run(['node','--input-type=module','-e',code],capture_output=True,text=True)
    assert r.returncode==0,r.stderr
