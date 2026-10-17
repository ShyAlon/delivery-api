"""An extra sandbox top-up gets its own purchase id and pack; the first top-up's id and the resume flags stay as they were."""
import re,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
import recovery_topup as rt
from delivery_api.payment_errors import PaymentRejected

SCRIPT=Path(__file__).resolve().parent.parent/'scripts/item6/vps/item6-resume.sh'
ENTRY=Path(__file__).resolve().parent.parent/'scripts/item6/resume_entry.py'


class Repo:
    def __init__(self):self.intents=[]
    def create_purchase(self,intent):self.intents.append(intent);return {'credit_posted':False}


class Svc:
    def __init__(self,*a):pass
    def create(self,purchase_id):return {'approval_url':'https://sandbox.example/'+purchase_id}


class Proc:
    class binding:live_mode=False


def run(monkeypatch,offer,seq):
    monkeypatch.setattr(rt,'PaymentService',Svc)
    repo=Repo();scope=type('S',(),{'owner_id':'11111111-1111-1111-1111-111111111111'})()
    review=rt.prepare_topup(scope=scope,run_id='a'*32,offer_id=offer,repository=repo,processor=Proc(),read_account=lambda s:{'units_per_credit':10**9,'balance_units':5},**({} if seq is None else {'sequence':seq}))
    return review,repo.intents[0]


def test_default_sequence_keeps_the_first_top_up_id(monkeypatch):
    review,intent=run(monkeypatch,'credits-12',None)
    assert intent.purchase_id=='item6-topup-'+'a'*32+'-01' and intent.credit_units==12*10**9


def test_extra_top_up_has_its_own_id_and_the_fifty_credit_pack(monkeypatch):
    review,intent=run(monkeypatch,'credits-50',2)
    assert intent.purchase_id=='item6-topup-'+'a'*32+'-02'
    assert intent.credit_units==50*10**9 and intent.amount_minor==4500 and intent.currency=='USD'
    assert review['sandbox'] is True and review['credit_units']==50*10**9


@pytest.mark.parametrize('seq',[0,10,'2',True,None.__class__])
def test_bad_sequence_is_refused(monkeypatch,seq):
    with pytest.raises((PaymentRejected,TypeError)):run(monkeypatch,'credits-50',seq)


def test_the_resume_entry_only_accepts_reviewed_packs_and_refuses_misuse():
    text=ENTRY.read_text()
    assert "choices=('credits-12','credits-50')" in text
    assert "an extra top-up applies to a later resume" in text and "needs its own purchase sequence" in text
    assert "topup_offer=a.topup_offer,topup_sequence=a.topup_sequence" in text


def test_the_resume_script_wires_the_extra_top_up_end_to_end():
    s=SCRIPT.read_text()
    assert re.search(r"case \"\$TOPUP_OFFER\" in ''\|credits-12\|credits-50\)",s)
    assert '--topup-offer $TOPUP_OFFER --topup-sequence $TOPSEQ' in s
    assert 'topup-webhook-$TOPSEQ' in s and "! -name 'topup-webhook*'" in s
    assert 'webhook-watch-topup.sh "$RID" "$CONT" 0$TOPSEQ topup-webhook-$TOPSEQ' in s
    assert 'os.environ["RID"]+"-"+os.environ["SEQ"]' in s and '"-01"' not in s
    assert s.count('TOPUP_OFFER=$TOPUP_OFFER TOPSEQ=$TOPSEQ')==2
    import subprocess;assert subprocess.run(['bash','-n',str(SCRIPT)]).returncode==0
