from copy import deepcopy
import hashlib,json
import pytest
from delivery_api.revision_install_review import installation_review_payload
from test_revision_lineage import setup as lineage_args


def args():
    from delivery_api.revision_lineage import plan_revision_lineage
    lineage=plan_revision_lineage(**lineage_args())
    return dict(lineage=lineage['lineage'],lineage_sha256=lineage['lineage_sha256'],proposal_document_sha256='d'*64,
        source_snapshot_sha256='b'*64,staged_receipt={'manifest_sha256':'c'*64,'checkpoint_id':'staged'},staged_event_sha256='e'*64)


def test_review_basis_is_stable_and_never_accepts_or_installs():
    a=args();before=deepcopy(a);review=installation_review_payload(**a)
    assert a==before and installation_review_payload(**a)==review
    body=review['review']
    assert body['state']=='awaiting_review' and not body['accepted'] and not body['installed'] and not body['installation_available']
    assert body['phases_requiring_reapproval'][0]=='srs' and body['cost_review']['requires_explicit_cost_acknowledgement']


@pytest.mark.parametrize('change',[{'proposal_document_sha256':'f'*64},{'source_snapshot_sha256':'bad'},
    {'staged_receipt':{'manifest_sha256':'c'*64,'checkpoint_id':'staged','accepted':True}}, {'staged_event_sha256':'bad'}])
def test_bad_review_basis_rejected(change):
    with pytest.raises(ValueError):installation_review_payload(**(args()|change))
