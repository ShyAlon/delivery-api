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


def test_operator_payment_period_rejects_unknown_owner_and_infinite_scope():
    import pytest
    from delivery_api.payment_reporting import validate_period
    for args in [(0,float('inf'),None),(1,0,None),(0,366*86400+1,None),(0,1,'foreign'),(True,2,None)]:
        with pytest.raises(ValueError):validate_period(*args)


def test_payment_operator_capability_never_inferred_from_analytics_or_admin():
    import pytest
    from delivery_api.operator_authorization import OperatorPrincipal
    from delivery_api.tenancy import TenantScope,tenant_context,TenantAccessDenied
    owner='00000000-0000-0000-0000-000000000001'
    class Store:
        def auth_session_active(self):return True
    with tenant_context(TenantScope(owner,owner)):
        with pytest.raises(TenantAccessDenied):OperatorPrincipal(owner,'v',frozenset({'analytics.read'})).authorize(Store(),'payments.read')
