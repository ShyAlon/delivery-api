"""Prepare/reconcile one new sandbox order. Caller owns reviewed payment scope.

No fresh signup, original-order replay, manual credit grant or live activation.
Purchase ID is stable, so failed create uses the persisted same request IDs.
"""
from delivery_api.payment_offers import purchase_intent
from delivery_api.payment_service import PaymentService
from delivery_api.payment_errors import PaymentRejected


def prepare_topup(*,scope,run_id,offer_id,repository,processor,read_account):
    if processor.binding.live_mode is not False:raise PaymentRejected('isolated sandbox top-up only')
    before=read_account(scope)
    if before['units_per_credit']!=10**9:raise PaymentRejected('original retail account scale required')
    intent=purchase_intent(purchase_id='item6-topup-'+run_id+'-01',owner_id=scope.owner_id,offer_id=offer_id,units_per_credit=before['units_per_credit'])
    # Immutable repository replay rejects a changed pack/owner/digest.
    row=repository.create_purchase(intent)
    if row['credit_posted']:raise PaymentRejected('already posted, reconcile existing purchase')
    approval=PaymentService(processor,repository).create(intent.purchase_id)
    return {'kind':'item6-sandbox-topup-review','approval':approval,'offer_id':offer_id,'amount_minor':intent.amount_minor,'currency':intent.currency,'credit_units':intent.credit_units,'intent_digest':intent.digest,'sandbox':True,'purchase_id':intent.purchase_id,'balance_before':before['balance_units'],'production_effect':False}


def finish_topup(*,review,scope,repository,processor,read_account,webhook_receipt):
    if processor.binding.live_mode is not False or review['sandbox'] is not True:raise PaymentRejected('sandbox only')
    row=repository.get_purchase(review['purchase_id']);intent=row['intent']
    if intent.owner_id!=scope.owner_id or intent.digest!=review['intent_digest']:raise PaymentRejected('exact reviewed top-up required')
    service=PaymentService(processor,repository)
    if not row['credit_posted']:
        capture=service.capture(intent.purchase_id)
        if capture.get('credit_posted') is not True:raise PaymentRejected('capture not confirmed; reconcile this purchase, never new order')
    raw,headers=webhook_receipt()
    event,_=processor.verify_event(raw,headers)
    service.webhook(raw,headers)
    row=repository.get_purchase(intent.purchase_id);after=read_account(scope)
    if not row['credit_posted'] or row['state']=='operator_review' or after['balance_units']!=review['balance_before']+intent.credit_units:raise PaymentRejected('top-up posting/balance mismatch')
    return {'kind':'item6-sandbox-topup','purchase_id':intent.purchase_id,'offer_id':intent.offer_id,'amount_minor':intent.amount_minor,'currency':intent.currency,'credit_units':intent.credit_units,'balance_before':review['balance_before'],'balance_after':after['balance_units'],'capture_id':row['capture_id'],'event_id':event['id'],'sandbox':True,'signature_verified':True,'production_effect':False,'new_purchase':True}
