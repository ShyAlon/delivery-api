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


def test_launcher_uses_shared_store_instead_of_per_provider_file_pins():
    script=(ROOT/'scripts/ops/preview-exec.sh').read_text()
    assert 'combine-provider-pins.py' not in script
    assert 'paid-pin' not in script and 'local-pin' not in script
    assert 'model-pins' in script or 'Model pins' in script
