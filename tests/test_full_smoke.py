"""full-smoke.py must say WHERE it died and WHY, every time.

2026-10-27: an image smoke failed and the operator saw only "smoke FAILED
(output above)" with nothing above. Two holes: full-smoke printed no stage
markers, and a failure outside the early-exit branch lost the entry log tail.
Here the engine package is deliberately absent, so the entry's preflight
refuses and full-smoke must name the stage, the reason, and the entry's own
report - a real subprocess run, no docker needed."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_a_refused_entry_is_reported_with_stage_reason_and_entry_report(tmp_path):
    # A genuinely isolated interpreter has dependencies and delivery, but no
    # engine or generator, even when this suite imports sibling checkouts.
    import os,site,venv
    venv.EnvBuilder(with_pip=False,symlinks=True).create(tmp_path/'runtime')
    python=tmp_path/'runtime/bin/python'
    target=tmp_path/'runtime/lib'/('python%d.%d'%sys.version_info[:2])/'site-packages'
    for source_dir in site.getsitepackages():
        for source in Path(source_dir).iterdir():
            if source.name.endswith('.pth') or source.name.startswith(('interview_engine','spec_generator','__editable__')):continue
            destination=target/source.name
            if not destination.exists():destination.symlink_to(source,target_is_directory=source.is_dir())
    (target/'delivery-source.pth').write_text(str(ROOT/'src')+'\n')
    probe=subprocess.run([str(python),'-I','-c',"import importlib.util; assert importlib.util.find_spec('interview_engine') is None; import delivery_api"],capture_output=True,text=True)
    assert probe.returncode==0,probe.stderr
    env={k:v for k,v in os.environ.items() if k not in ('DELIVERY_TEST_POSTGRES_DSN','DELIVERY_TEST_POSTGRES_URL','DELIVERY_DATABASE_URL','PYTHONPATH')}
    out = subprocess.run([sys.executable,str(ROOT/'scripts/docker/owned-pg-smoke.py'),str(ROOT),str(python),str(ROOT/'scripts/docker/full-smoke.py')],
                         env=env,capture_output=True,text=True,timeout=90)
    assert out.returncode != 0
    text = out.stdout + out.stderr
    assert 'smoke stage:' in text,text
    assert 'SMOKE FAILED' in text,text
    assert 'engine-compatible' in text,text
