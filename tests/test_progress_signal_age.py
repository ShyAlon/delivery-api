"""Signal age must come from event time,never epoch-zero or run start."""
from test_artifacts import evidence_path
import pytest


@pytest.mark.parametrize('event,expected',[
    ({'kind':'progress','message':'Drafting','payload':{}},None),
    ({'kind':'progress','message':'Drafting','ts':0,'payload':{}},None),
    ({'kind':'progress','message':'Drafting','ts':'invalid','payload':{'timestamp':'invalid'}},None),
    ({'kind':'progress','message':'Drafting','payload':{'stage':'draft','status':'started'}},None),
    ({'kind':'progress','message':'Drafting','ts':1999999995,'payload':{}},5),
    ({'kind':'heartbeat','message':'Still working','ts':1999999993,'payload':{'timestamp':'invalid'}},7),
    ({'kind':'progress','message':'Drafting','ts':1,'payload':{'timestamp':'2033-05-18T03:33:16Z'}},4),
])
def test_signal_age_requires_valid_event_time(pg_browser,event,expected):
    page,base,_=pg_browser
    page.goto(base+'/about')
    result=page.evaluate('''async event=>{
      const {stageProgressHtml}=await import('/assets/progress.js');
      const original=Date.now;Date.now=()=>2000000000000;
      try{return stageProgressHtml([event],{startedAt:1999999900})}
      finally{Date.now=original}
    }''',event)
    assert '100s elapsed' in result
    assert 'Drafting' in result or 'Still working' in result
    if expected is None:
        assert 'last engine signal' not in result
    else:
        assert f'last engine signal {expected}s ago' in result

    if expected is None and 'ts' not in event and not event['payload']:
        page.evaluate("html=>document.querySelector('#app').innerHTML=html",result)
        page.screenshot(path=evidence_path('progress-unknown-signal-time.png'))
