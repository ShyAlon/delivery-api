import pytest
from delivery_api.srs_rework_job import _request

OPTIONS={'gate_revision':1,'source_checkpoint_id':'checkpoint','source_review_revision':'a'*64,'item_ids':['R-1'],'guidance':'Behavior only'}

@pytest.mark.parametrize('change',[{'gate_revision':True},{'gate_revision':-1},{'item_ids':['R-1','R-1']},{'item_ids':[None]},{'source_review_revision':'wrong'},{'extra':'unknown'}])
def test_exact_job_options_reject_ambiguous_or_unbounded_inputs(change):
    with pytest.raises(ValueError):_request(OPTIONS|change)


def test_revision_is_retained_not_inferred_at_execution():
    assert _request(OPTIONS)['gate_revision']==1
