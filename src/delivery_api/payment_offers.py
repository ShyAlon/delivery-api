"""Owner-reviewed Q01 USD offers. Configuration only, no enabled checkout.

Source workbook SHA256 14b6a75930f26ab04a34d8ab2dba3c12fcf352a95fc78f21f13eaf8648d03eb8
answer!C5; owner confirmed "As proposed" Oct10 2026 08:06:07 Asia/Jerusalem.
Credits remain distinct from provider expense. No scale is silently selected.
"""
from .payment_contract import PurchaseIntent,MAX_BIGINT
OFFERS_VERSION='q01-20261010-v1'
OFFERS={'credits-5':(5,500),'credits-12':(12,1150),'credits-25':(25,2300),'credits-50':(50,4500)}


def purchase_intent(*,purchase_id,owner_id,offer_id,units_per_credit):
    if offer_id not in OFFERS:raise ValueError('reviewed offer ID required')
    if type(units_per_credit) is not int or not 0<units_per_credit<=MAX_BIGINT:
        raise ValueError('explicit provisioned account credit scale required')
    credits,amount=OFFERS[offer_id]
    return PurchaseIntent(purchase_id,owner_id,OFFERS_VERSION,offer_id,'USD',amount,credits*units_per_credit,units_per_credit)
