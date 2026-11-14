from copy import deepcopy
from delivery_api.decision_provenance import project_decisions,SUPPORT_RULE
from vmodel_contracts.semantic_review import digest


def _adr():
    source={'item_id':'FD-1','status':'decided','decision':'Do nothing on an unreadable page','reason':'Keep it quiet'}
    citation={'source_ref':'FD-1','source_revision':digest(source),'source':source}
    return {'decision_id':'ADR-1','title':'Read the page','driver':'founder_constraint','decision':'Use a content script',
            'rationale':'It can read the active DOM','founder_decision_refs':['FD-1'],'founder_citations':[citation]}


def test_retained_constraint_is_not_a_technology_preference_or_founder_approval():
    adr=_adr();row=project_decisions({'decisions':[adr]},[],{'availability':'available'})[0]
    assert row['founder_evidence']==[]
    assert row['founder_constraints']==adr['founder_citations']
    assert row['model_rationale']['rationale']=='It can read the active DOM'
    assert row['support_review']['status']=='support_not_yet_reviewed'
    assert row['support_review']['finding_availability']=='not_published'
    assert 'needs review' in row['label']


def test_exact_saved_finding_disposition_does_not_apply_to_changed_adr():
    adr=_adr();finding={'finding_id':'F-fixture','rule_id':SUPPORT_RULE,'targets':['ADR-1'],
        'artifact_revision':digest({'adr':adr,'sources':adr['founder_citations']}),'status':'OPEN','summary':'Compare the source'}
    ledger={'findings':{'F-fixture':finding}}
    row=project_decisions({'decisions':[adr]},[],{},ledger)[0]
    assert row['support_review']['status']=='open'
    assert row['support_review']['findings'][0]['finding_id']=='F-fixture'
    finding['status']='REJECTED';finding['disposition']={'reason':'Accepted risk'}
    assert project_decisions({'decisions':[adr]},[],{},ledger)[0]['support_review']['status']=='recorded_disposition'
    changed=deepcopy(adr);changed['rationale']='Changed reasoning'
    assert project_decisions({'decisions':[changed]},[],{},ledger)[0]['support_review']['status']=='support_not_yet_reviewed'


def test_invalid_snapshot_never_becomes_founder_evidence():
    adr=_adr();adr['founder_citations'][0]['source']['decision']='Altered source'
    row=project_decisions({'decisions':[adr]},[],{},[])[0]
    assert row['founder_constraints']==[] and row['constraint_status']=='invalid_snapshot'
    assert row['support_review'] is None


def test_technology_preference_channel_keeps_exact_matching_and_source_turns():
    adr=_adr();adr['technology_selections']=[{'slot_id':'slot:language','display_name':'TypeScript'}]
    pref={'area':'language','preference':'TypeScript','strength':'preferred','source_turns':[4]}
    assert project_decisions({'decisions':[adr]},[pref],{})[0]['founder_evidence']==[pref]
    pref['source_turns']=[]
    assert project_decisions({'decisions':[adr]},[pref],{})[0]['founder_evidence']==[]
