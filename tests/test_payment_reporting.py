from delivery_api.payment_reporting import receiving_report


def test_unknown_receiving_cost_is_not_zero_profit_and_currency_stays_separate():
    base={'processor':'fictional','merchant_account':'merchant','live_mode':False,'owner_id':'owner',
        'offer_version':'v1','offer_id':'pack','currency':'USD','amount_minor':500,
        'processor_fee_minor':None,'net_received_minor':None,'state':'fulfilled'}
    rows=[base,dict(base,processor_fee_minor=61,net_received_minor=439),dict(base,currency='EUR',state='operator_review')]
    report=receiving_report(rows)
    assert len(report['groups'])==2
    usd=next(g for g in report['groups'] if g['currency']=='USD')
    assert usd['gross_minor']==1000 and usd['known_net_minor']==439 and usd['unknown_receiving_cost_count']==1
    assert not report['profit_available']
