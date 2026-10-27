"""Compose only independently pinned public/local config, no shell eval/network."""
import importlib.util,json,subprocess,sys
from pathlib import Path
import pytest
from test_production_gateway import dual_env
ROOT=Path(__file__).resolve().parents[1]

def test_composes_both_pins_without_repricing_or_persisting_key(tmp_path):
    env=dual_env();rows=json.loads(env['VMODEL_REVIEWED_MODEL_PRICES']);contexts=json.loads(env['VMODEL_REVIEWED_CONTEXT_CEILING'])
    inputs=[]
    for row,context in zip(rows,contexts):
        d=tmp_path/row['provider'];d.mkdir();context.pop('provider')
        (d/'cloud-models.env').write_text('VMODEL_REVIEWED_MODEL_PRICES='+json.dumps([row])+'\nVMODEL_REVIEWED_CONTEXT_CEILING='+json.dumps(context)+'\n')
        inputs.append(d)
    out=tmp_path/'out';out.mkdir()
    result=subprocess.run([sys.executable,str(ROOT/'scripts/ops/combine-provider-pins.py'),str(out),*[str(d) for d in inputs]],capture_output=True,text=True)
    assert result.returncode==0,result.stderr+result.stdout
    values=dict(line.split('=',1) for line in (out/'cloud-models.env').read_text().splitlines() if line and not line.startswith('#'))
    assert set(values)=={'VMODEL_REVIEWED_MODEL_PRICES','VMODEL_REVIEWED_CONTEXT_CEILING'}
    assert json.loads(values['VMODEL_REVIEWED_MODEL_PRICES'])==rows
    assert json.loads(values['VMODEL_REVIEWED_CONTEXT_CEILING'])==[dict(c,provider=r['provider']) for c,r in zip(contexts,rows)]
    assert 'test-key' not in (out/'cloud-models.env').read_text()


def test_both_shell_pin_branch_passes_repo_root_only_to_local_pin(tmp_path):
    import os
    fake=tmp_path/'fake-python'
    fake.write_text("#!/usr/bin/env python3\nimport sys,os,pathlib,json\nwith open(os.environ['CALL_LOG'],'a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')\n")
    fake.chmod(0o755)
    script=(ROOT/'scripts/ops/preview-exec.sh').read_text()
    start=script.index('if [ \"$PROVIDER\" = both ]; then\n  mkdir')
    end=script.index('\nif [ \"$rc\" != 0 ]; then',start)
    branch=script[start:end].replace('.venv/bin/python','\"'+str(fake)+'\"')
    # dirname of $0 must still point to the actual ops directory.
    log=tmp_path/'calls'
    result=subprocess.run(['bash','-c',branch,str(ROOT/'scripts/ops/preview-exec.sh')],env={**os.environ,'CALL_LOG':str(log),'PROVIDER':'both','WORK':str(tmp_path),'ROOT':'/candidate/repos','OLLAMA_MODEL':'phi4','OLLAMA_HOST':'http://local'},text=True,capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr
    calls=[json.loads(line) for line in log.read_text().splitlines()]
    assert calls==[[str(ROOT/'scripts/ops/pin.py'),str(tmp_path/'paid-pin')],
        [str(ROOT/'scripts/ops/pin-local.py'),str(tmp_path/'local-pin'),'/candidate/repos'],
        [str(ROOT/'scripts/ops/combine-provider-pins.py'),str(tmp_path),str(tmp_path/'paid-pin'),str(tmp_path/'local-pin')]]
