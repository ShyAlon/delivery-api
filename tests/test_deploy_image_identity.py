import importlib.util
import json
from pathlib import Path
import sys
import pytest

D=Path(__file__).resolve().parents[1]/'scripts/docker';sys.path.insert(0,str(D))
spec=importlib.util.spec_from_file_location('deploy_identity',D/'deploy_identity.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
import image_receipt
ID='sha256:'+'a'*64
SOURCES={name:'b'*40 for name in image_receipt.REPOS}


def inputs():
    manifest=json.dumps({'schemaVersion':2,'mediaType':'application/vnd.oci.image.manifest.v1+json',
                         'config':{'digest':ID,'size':123},'layers':[{'digest':ID,'size':234}]}).encode()
    md=image_receipt.digest(manifest);repo='ghcr.io/shyalon/require'
    receipt={'schema':1,'repository':repo,'manifest_digest':md,'config_digest':ID,'platform':'linux/amd64',
             'sources':SOURCES,'runtime_contract':ID,'acceptance_contract':ID,'checks':{name:'passed' for name in image_receipt.CHECKS}}
    return dict(raw=json.dumps(receipt).encode(),sources=SOURCES,runtime_contract=ID,acceptance_contract=ID,
                repository=repo,manifest=manifest,image={'Id':ID,'Os':'linux','Architecture':'amd64','RepoDigests':[repo+'@'+md]},
                name='requi-service',env_file='/etc/requi/full.env',port=8000,network='service')


def test_review_only_emits_immutable_reference_and_shared_runtime():
    result=m.review(**inputs())
    assert result['reference'].startswith('ghcr.io/shyalon/require@sha256:')
    assert result['docker_arguments'][-1]==ID
    assert result['apply_authorized'] is False
    assert '--read-only' in result['docker_arguments']


@pytest.mark.parametrize('change',[{'repository':'ghcr.io/shyalon/other'}, {'manifest':b'{}'},
                                   {'image':{'Id':'sha256:'+'c'*64}}, {'runtime_contract':'sha256:'+'c'*64},
                                   {'acceptance_contract':'sha256:'+'c'*64}, {'sources':{}}])
def test_different_digest_contract_or_repository_cannot_deploy(change):
    with pytest.raises((image_receipt.InvalidReceipt,ValueError)):m.review(**(inputs()|change))
