"""Pure aggregation of authorized payment cost snapshots, never profit claims.

Actual receiving costs are separate from provider expense, FX/withdrawal/tax
and consumption attribution. Missing evidence is unknown, never zero.
"""


def receiving_report(rows):
    groups={}
    for row in rows:
        key=(row['processor'],row['merchant_account'],row['live_mode'],row['owner_id'],row['offer_version'],row['offer_id'],row['currency'])
        value=groups.setdefault(key,{'processor':key[0],'merchant_account':key[1],'live_mode':key[2],
            'owner_id':key[3],'offer_version':key[4],'offer_id':key[5],'currency':key[6],
            'purchases':0,'gross_minor':0,'known_fee_minor':0,'known_net_minor':0,
            'unknown_receiving_cost_count':0,'operator_review_count':0})
        value['purchases']+=1;value['gross_minor']+=row['amount_minor']
        if row['processor_fee_minor'] is None or row['net_received_minor'] is None:
            value['unknown_receiving_cost_count']+=1
        else:
            value['known_fee_minor']+=row['processor_fee_minor'];value['known_net_minor']+=row['net_received_minor']
        if row['state']=='operator_review':value['operator_review_count']+=1
    return {'groups':list(groups.values()),'profit_available':False,
        'scope':'confirmed receipt gross/processor fee/net, not accounting profit',
        'missing_cost_categories':['withdrawal/FX','sales/income taxes','refund/reversal/dispute losses and fees',
            'hosting/operations','provider cost attribution to purchased credit lots'],
        'credit_purchase_does_not_bound_provider_expense':True}


def validate_period(start_at,end_at,owner_id=None):
    import math
    if any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in (start_at,end_at)) or not start_at<end_at or end_at-start_at>366*86400:
        raise ValueError('explicit bounded report period required')
    if owner_id is not None:
        from uuid import UUID
        try:
            if str(UUID(owner_id))!=owner_id:raise ValueError()
        except (ValueError,TypeError,AttributeError) as exc:raise ValueError('canonical report owner required') from exc


def cost_caveats():
    return {'profit_available':False,'scope_description':'confirmed receipt gross/processor fee/net, not accounting profit',
        'missing_cost_categories':['withdrawal/FX','sales/income taxes','refund/reversal/dispute losses and fees',
            'hosting/operations','provider cost attribution to purchased credit lots'],
        'credit_purchase_does_not_bound_provider_expense':True}
