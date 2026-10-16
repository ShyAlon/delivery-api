import json
import pytest
from attended import Attended

def test_gate_needs_no_digest_paste_and_binds_the_displayed_digest(tmp_path,monkeypatch):
 monkeypatch.setattr('builtins.input',lambda prompt:'accept')
 out=Attended(tmp_path,lambda:'owner').gate({'gate':'srs'},'a'*64)
 assert out=={'review_sha256':'a'*64,'decision':{'action':'accept'}}
 assert (tmp_path/'review-001.json').stat().st_mode&0o777==0o600

def test_gate_rejects_an_unknown_choice(tmp_path,monkeypatch):
 monkeypatch.setattr('builtins.input',lambda prompt:'wrong')
 with pytest.raises(ValueError,match='explicit'):Attended(tmp_path,lambda:'owner').gate({'gate':'srs'},'a'*64)

def test_fresh_review_requires_explicit_cost_ack(tmp_path,monkeypatch):
 monkeypatch.setattr('builtins.input',lambda prompt:'no')
 with pytest.raises(ValueError,match='approval'):Attended(tmp_path,lambda:'owner').approve_revision({'review':{},'review_sha256':'a'*64})

def test_webhook_waits_for_the_exported_file_instead_of_prompting(tmp_path,monkeypatch):
 f=tmp_path/'webhook-1.json';f.write_text(json.dumps({'event':{'a':1},'headers':{'h':'v'}}));f.chmod(0o600)
 monkeypatch.setenv('ITEM6_WEBHOOK_DIR',str(tmp_path))
 monkeypatch.setattr('builtins.input',lambda prompt:pytest.fail('must not prompt'))
 raw,headers=Attended(tmp_path,lambda:'owner').webhook()
 assert json.loads(raw)=={'a':1} and headers=={'h':'v'}

def test_webhook_refuses_two_exported_files(tmp_path,monkeypatch):
 for n in ('1','2'):(tmp_path/f'webhook-{n}.json').write_text('{}')
 monkeypatch.setenv('ITEM6_WEBHOOK_DIR',str(tmp_path))
 with pytest.raises(ValueError,match='exactly one'):Attended(tmp_path,lambda:'owner').webhook()

def test_webhook_falls_back_to_a_typed_path_after_the_wait(tmp_path,monkeypatch):
 f=tmp_path/'x.json';f.write_text(json.dumps({'event':{},'headers':{}}));f.chmod(0o600)
 monkeypatch.setenv('ITEM6_WEBHOOK_DIR',str(tmp_path/'empty'));monkeypatch.setenv('ITEM6_WEBHOOK_WAIT_SECONDS','0')
 monkeypatch.setattr('builtins.input',lambda prompt:str(f))
 raw,_=Attended(tmp_path,lambda:'owner').webhook()
 assert raw==b'{}'

def test_semantic_review_choices_assemble_the_exact_decision_json(tmp_path):
 review={'revision':'b'*64,'findings':[{'item_id':'i1','path':['a',0],'rule':'r','reason':'why','original':{'x':1}},{'item_id':'i1','path':['b'],'rule':'r2','reason':'w2','original':2},{'item_id':'i2','path':['c'],'rule':'r','reason':'w','original':'t'}]}
 answers=iter(['accept','looks right','replace','','{"x":2}'])
 value=Attended(tmp_path,lambda:'owner').semantic_value(review,lambda prompt:next(answers))
 assert json.loads(value)=={'revision':'b'*64,'decisions':[{'item_id':'i1','action':'accept','actor':'owner','rationale':'looks right'},{'item_id':'i2','action':'replace','actor':'owner','rationale':'','replacement':{'x':2}}]}

def test_semantic_review_rejects_an_unknown_choice(tmp_path):
 review={'revision':'b'*64,'findings':[{'item_id':'i1','path':['a'],'rule':'r','reason':'w','original':1}]}
 with pytest.raises(ValueError,match='explicit'):Attended(tmp_path,lambda:'owner').semantic_value(review,lambda prompt:'yes')

def test_paypal_step_prints_the_link_and_never_prompts(tmp_path,monkeypatch,capsys):
 monkeypatch.setattr('builtins.input',lambda prompt:pytest.fail('must not prompt'))
 Attended(tmp_path,lambda:'owner').paypal({'approval_url':'https://example.test/approve'})
 assert 'example.test/approve' in capsys.readouterr().out
