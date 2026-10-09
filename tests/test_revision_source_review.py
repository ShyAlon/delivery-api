from copy import deepcopy
import pytest
from delivery_api.revision_source_review import source_review_payload


def args():
    return dict(lineage_sha256='a'*64,checkpoint_id='checkpoint',semantic_session_id='semantic',phase='srs',
        upstream_sha256={k:'b'*64 for k in ('interview/intent_model.json','interview/conops.json','spec/needs.json','spec/system-baseline.json')})


def test_source_snapshot_is_stable_awaiting_review_not_permission():
    a=args();before=deepcopy(a);one=source_review_payload(**a)
    assert one==source_review_payload(**a) and a==before
    assert one['snapshot']['state']=='awaiting_review' and not one['snapshot']['accepted'] and not one['snapshot']['installed']


@pytest.mark.parametrize('key,value',[('phase','needs'),('semantic_session_id','../escape'),('lineage_sha256','bad'),('checkpoint_id','')])
def test_rejects_unbound_review_input(key,value):
    a=args();a[key]=value
    with pytest.raises(ValueError):source_review_payload(**a)


def test_inventory_exact_and_architecture_requires_approved_srs():
    a=args();a['upstream_sha256'].pop('interview/conops.json')
    with pytest.raises(ValueError):source_review_payload(**a)
    a=args();a['phase']='architecture'
    with pytest.raises(ValueError):source_review_payload(**a)
    a['upstream_sha256']['spec/srs.json']='c'*64
    assert source_review_payload(**a)['snapshot']['phase']=='architecture'
