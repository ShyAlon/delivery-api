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
