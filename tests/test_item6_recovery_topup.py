import sys
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/item6'))
import recovery_topup as m
from delivery_api.payment_errors import PaymentRejected


def test_existing_balance_and_stable_new_purchase_preserved(monkeypatch):
 account={'balance_units':362835621,'units_per_credit':10**9};scope=SimpleNamespace(owner_id='00000000-0000-0000-0000-000000000001')
 rows={};created=[]
 class Repo:
  def create_purchase(self,intent):
   if intent.purchase_id in rows and rows[intent.purchase_id]['intent']!=intent:raise PaymentRejected('conflict')
   rows.setdefault(intent.purchase_id,{'intent':intent,'credit_posted':False,'state':'created','capture_id':None,'order_id':'fixture-order'});created.append(intent.purchase_id);return rows[intent.purchase_id]
  def get_purchase(self,pid):return dict(rows[pid])
 class Service:
  def __init__(self,*a):pass
  def create(self,pid):return {'approval_url':'fixture-only'}
  def capture(self,pid):
   rows[pid].update(credit_posted=True,state='fulfilled',capture_id='fixture-capture');account['balance_units']+=rows[pid]['intent'].credit_units
   return {'credit_posted':True}
  def webhook(self,*a):return {'credit_posted':False}
 monkeypatch.setattr(m,'PaymentService',Service)
 repo=Repo();processor=SimpleNamespace(binding=SimpleNamespace(live_mode=False),verify_event=lambda *a:({'id':'fixture-event'},'a'*64),payment_notice=lambda e:{'action':'paid','order_id':'fixture-order','payment_id':'fixture-capture'})
 review=m.prepare_topup(scope=scope,run_id='original',offer_id='credits-5',repository=repo,processor=processor,read_account=lambda s:dict(account))
 assert review['balance_before']==362835621 and review['credit_units']==5*10**9 and review['amount_minor']==500
 assert review==m.prepare_topup(scope=scope,run_id='original',offer_id='credits-5',repository=repo,processor=processor,read_account=lambda s:dict(account))
 with pytest.raises(PaymentRejected):m.prepare_topup(scope=scope,run_id='original',offer_id='credits-12',repository=repo,processor=processor,read_account=lambda s:dict(account))
 result=m.finish_topup(review=review,scope=scope,repository=repo,processor=processor,read_account=lambda s:dict(account),webhook_receipt=lambda:(b'fixture',{}))
 assert result['balance_after']==5362835621
 assert m.finish_topup(review=review,scope=scope,repository=repo,processor=processor,read_account=lambda s:dict(account),webhook_receipt=lambda:(b'fixture',{}))==result
 assert account['balance_units']==5362835621
 processor.payment_notice=lambda e:{'action':'paid','order_id':'wrong','payment_id':'fixture-capture'}
 with pytest.raises(PaymentRejected,match='webhook'):m.finish_topup(review=review,scope=scope,repository=repo,processor=processor,read_account=lambda s:dict(account),webhook_receipt=lambda:(b'fixture',{}))
 processor.binding.live_mode=True
 with pytest.raises(PaymentRejected):m.prepare_topup(scope=scope,run_id='original',offer_id='credits-5',repository=repo,processor=processor,read_account=lambda s:dict(account))
