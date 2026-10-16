import source_check as sc


def good_read():return {'observed_at':'2026-10-16T18:00:00+00:00'}


def test_all_pass():
    lines=[]
    failed=sc.run_checks({},good_read,lambda s,cached:True,resolve=lambda h,p:[1],say=lines.append)
    assert failed==[] and len(lines)==4 and all(l[:2]=='20' and ' +' in l for l in lines)


def test_vendor_failure_shows_the_real_message_and_does_not_hide_the_other_checks():
    def verify(s,cached):
        assert cached is False
        raise ValueError('vendor source changed during run: pinned terms differ (published_usd_per_million)')
    lines=[]
    failed=sc.run_checks({},good_read,verify,resolve=lambda h,p:[1],say=lines.append)
    assert failed==['vendor-pages']
    assert any('published_usd_per_million' in l and 'FAIL vendor pages' in l for l in lines)
    assert any('ok   DeepSeek balance' in l for l in lines)


def test_dns_and_balance_failures_are_named():
    def boom(*a):raise OSError('Temporary failure in name resolution')
    def bad():raise ValueError('provider balance read unavailable')
    lines=[]
    failed=sc.run_checks({},bad,lambda s,cached:True,resolve=boom,say=lines.append)
    assert failed==['dns','dns','balance']
    assert any('Temporary failure' in l for l in lines)
