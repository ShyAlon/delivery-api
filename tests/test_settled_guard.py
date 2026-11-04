"""Settled-work substitution guard: a phase child may write its own phase's outputs,
never artifacts of phases already completed. Pinned before child start, checked
before publication. The canonical case: a pickup host's generator resume regenerates
architecture over the founder-approved bytes (no companion check involved)."""
import hashlib, json
import pytest
from delivery_api.artifact_publication import format_settled_substitution
from delivery_api.generator_documents import pin_document_artifacts,document_substitutions

class Documents:
    def __init__(self):
        self.data={'spec/srs.json':b'srs','spec/srs.semantic-review.json':b'review',
                   'spec/architecture.json':b'arch','spec/architecture.decisions.json':b'decisions'}
    def capture(self,keys):return {key:self.data[key] for key in keys if key in self.data}

def fixture():
    return {'phases':[{'phase_key':'srs','state':'approved'},{'phase_key':'architecture','state':'awaiting_approval'},{'phase_key':'specs','state':'running'}]},Documents()

@pytest.mark.parametrize('change,expected',[('changed',[('changed','architecture','architecture.json')]),
    ('vanished',[('vanished','architecture','architecture.decisions.json')]),
    ('appeared',[('appeared','architecture','architecture.json')]),('current',[]),('identical',[])])
def test_document_settled_substitutions(change,expected):
    task,docs=fixture()
    if change=='appeared':del docs.data['spec/architecture.json']
    pinned,settled=pin_document_artifacts(task,docs,current_phase='specs')
    if change in ('changed','appeared'):docs.data['spec/architecture.json']=b'changed'
    elif change=='vanished':del docs.data['spec/architecture.decisions.json']
    elif change=='current':docs.data.update({'spec/items.json':b'[]','spec/run.log':b'log','spec/architecture.md':b'view'})
    assert document_substitutions(docs,pinned,settled)==expected
    if expected:
        message=format_settled_substitution('specs',expected)
        assert 'never regenerated' in message and 'nothing from this run was published' in message
        assert 'restored' not in message
