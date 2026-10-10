"""Concrete private identity/payment evidence adapters. No mounted app routes.

Hermetic network clients stay injected. mac_acceptance never accepts fixtures as
identity/signup evidence. This module never retrieves credentials or starts work.
"""
import hashlib,json
from delivery_api.tenancy import tenant_context
from delivery_api.supabase_auth import SupabaseIdentityVerifier
from delivery_api.operator_authorization import OperatorPrincipal
from delivery_api.payment_offers import purchase_intent
from delivery_api.payment_service import PaymentService


class IdentityAdapter:
    def __init__(self,*,signup,verifier,store):
        self.signup=signup;self.verifier=verifier;self.store=store;self.scope=None

    def __call__(self,isolation,previous):
        response=self.signup()
        # Signup adapter proves observed new identity through its integration,
        # never through email/metadata or a user-selected owner ID.
        if not isinstance(response,dict) or set(response)!={'token','fresh_signup','source_mode'}:
            raise ValueError('fixed signup response required')
        if response['fresh_signup'] is not True or response['source_mode']!=isolation.mode:
            raise ValueError('new observed signup in matching mode required')
        scope=self.verifier.verify(response['token'])
        with tenant_context(scope):
            if self.store.auth_session_active() is not True:raise ValueError('currently active verified session required')
        self.scope=scope
        return base(isolation,'identity')|{'subject':scope.owner_id,'session_id':scope.session_id,
            'signature_verified':True,'active_session':True,'fresh_signup':True}


class OperatorAdapter:
    def __init__(self,identity,*,store,read_reviewed_registry):
        self.identity=identity;self.store=store;self.read_registry=read_reviewed_registry
    def __call__(self,isolation,previous):
        principal=self.read_registry()
        if not isinstance(principal,OperatorPrincipal):raise ValueError('explicit reviewed registry principal required')
        with tenant_context(self.identity.scope):
            evidence=principal.authorize(self.store,'payments.read')
        return base(isolation,'operator')|{'subject':evidence['operator_subject'],
            'registry_version':evidence['registry_version'],'explicit_grant':True,'capabilities':sorted(principal.capabilities)}


class SandboxPurchaseAdapter:
    def __init__(self,identity,*,repository,processor,approve,webhook_receipt,read_balance,units_per_credit=1000):
        if processor.binding.live_mode is not False:raise ValueError('PayPal sandbox only')
        self.identity=identity;self.repo=repository;self.processor=processor;self.approve=approve
        self.webhook=webhook_receipt;self.balance=read_balance
        if units_per_credit not in (1000,10**9):raise ValueError('known acceptance retail scale required')
        self.scale=units_per_credit
    def __call__(self,isolation,previous):
        if self.processor.binding.live_mode is not False:raise ValueError('sandbox binding changed')
        scope=self.identity.scope
        intent=purchase_intent(purchase_id='item6-'+isolation.run_id,owner_id=scope.owner_id,offer_id='credits-5',units_per_credit=self.scale)
        # Account provisioning is explicit reviewed fixture setup. Never overwrite
        # a balance or try another purchase to hide an uncertain prior capture.
        if self.balance(scope)!=0:raise ValueError('fresh zero balance required before purchase')
        self.repo.create_purchase(intent)
        service=PaymentService(self.processor,self.repo);approval=service.create(intent.purchase_id)
        self.approve(approval)
        first=service.capture(intent.purchase_id)
        if first.get('credit_posted') is not True:raise ValueError('capture not confirmed; stop and reconcile existing purchase')
        raw,headers=self.webhook()
        event,_=self.processor.verify_event(raw,headers)
        one=service.webhook(raw,headers);two=service.webhook(raw,headers)
        replay=service.capture(intent.purchase_id)
        row=self.repo.get_purchase(intent.purchase_id)
        if one!=two or replay.get('credit_posted') is not False:raise ValueError('payment replay did not remain once-only')
        if not row['credit_posted'] or self.balance(scope)!=5*self.scale:raise ValueError('durable balance/posting mismatch')
        return base(isolation,'sandbox_purchase')|{'subject':scope.owner_id,'purchase_id':intent.purchase_id,
            'payment_id':row['capture_id'],'merchant_id':self.processor.binding.merchant_id,'sandbox':True,
            'amount_minor':intent.amount_minor,'credit_units':intent.credit_units,'posted_count':1,
            'signature_verified':True,'balance_units':self.balance(scope)}


def base(isolation,stage):
    return {'stage':stage,'run_id':isolation.run_id,'source_mode':isolation.mode,'production_effect':False}
