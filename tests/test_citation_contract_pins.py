from pathlib import Path
import hashlib,zipfile
from vmodel_contracts import CONTRACT_VERSION

ROOT=Path(__file__).parents[1]

def test_runtime_paths_use_the_exact_source_snapshot_contract():
 wheel=ROOT/'vendor/vmodel_contracts-0.29.0-py3-none-any.whl'
 sha=hashlib.sha256(wheel.read_bytes()).hexdigest()
 assert CONTRACT_VERSION=='0.29.0'
 assert sha in (ROOT/'uv.lock').read_text()
 for name in ('Dockerfile','Dockerfile.full'):
  text=(ROOT/name).read_text()
  assert sha in text and 'vmodel_contracts-0.29.0' in text and 'vmodel_contracts-0.28.0' not in text
 for name in ('scripts/test-local.sh','scripts/ops/lib.sh','scripts/ops/venv_check.py','scripts/owner/answer-candidate-test.py','.github/workflows/ci-cd.yml'):
  text=(ROOT/name).read_text()
  assert 'vmodel_contracts-0.29.0' in text and 'vmodel_contracts-0.28.0' not in text
 with zipfile.ZipFile(wheel) as z:assert 'vmodel_contracts/citations.py' in z.namelist()

def test_real_acceptance_producers_pin_the_same_public_contract():
 for name in ('interview_engine','spec_generator'):
  wheel=ROOT/f'vendor/{name}-0.1.0-py3-none-any.whl'
  with zipfile.ZipFile(wheel) as z:
   metadata=z.read(f'{name}-0.1.0.dist-info/METADATA').decode()
   assert 'vmodel-contracts==0.29.0' in metadata
