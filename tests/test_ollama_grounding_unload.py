"""Grounding must release only the model it loaded, even when inspection fails."""
import json,os,runpy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).parents[1]

@pytest.mark.parametrize('inspection_error',[False,True])
def test_grounding_unloads_its_model_on_success_or_failure(tmp_path,monkeypatch,inspection_error):
    requests=[];loaded=False
    class Response:
        def __init__(self,value):self.value=value
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps(self.value).encode()
    def urlopen(req,timeout):
        nonlocal loaded
        payload=json.loads(req.data) if req.data else None;requests.append((req.full_url,payload))
        if req.full_url.endswith('/api/generate'):
            loaded=payload['keep_alive']!=0;return Response({})
        if req.full_url.endswith('/api/ps'):
            if loaded and inspection_error:raise OSError('inspection failed')
            return Response({'models':[{'model':'phi4','context_length':32768}] if loaded else []})
        return Response({'version':'fixture'})
    monkeypatch.setattr('urllib.request.urlopen',urlopen)
    monkeypatch.setattr(sys,'argv',['pin-local.py',str(tmp_path),str(tmp_path/'missing-repos')])
    with pytest.raises(SystemExit):runpy.run_path(str(ROOT/'scripts/ops/pin-local.py'),run_name='__main__')
    generations=[payload for _,payload in requests if payload is not None and 'keep_alive' in payload]
    assert generations[-1]['keep_alive']==0 and not loaded
    assert generations[-1]['prompt']=='' and generations[-1]['model']=='phi4'

def test_preview_app_has_short_idle_retention(tmp_path):
    import subprocess
    result=subprocess.run(['bash','-c','. "$1"; preview_ollama_env "$2" phi4 http://localhost:11434','_',str(ROOT/'scripts/ops/lib.sh'),str(tmp_path/'env')],capture_output=True,text=True,env={'PATH':'/usr/bin:/bin'})
    assert result.returncode==0
    assert 'VMODEL_OLLAMA_KEEP_ALIVE=30s' in (tmp_path/'env').read_text()

def test_grounding_does_not_unload_a_preexisting_model(tmp_path,monkeypatch):
    requests=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'models':[{'model':'phi4','context_length':32768}],'version':'fixture'}).encode()
    def urlopen(req,timeout):requests.append(req);return Response()
    monkeypatch.setattr('urllib.request.urlopen',urlopen)
    monkeypatch.setattr(sys,'argv',['pin-local.py',str(tmp_path),str(tmp_path/'missing-repos')])
    with pytest.raises(SystemExit):runpy.run_path(str(ROOT/'scripts/ops/pin-local.py'),run_name='__main__')
    assert not any(req.full_url.endswith('/api/generate') for req in requests)
