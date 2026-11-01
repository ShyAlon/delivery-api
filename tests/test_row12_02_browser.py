from test_artifacts import evidence_path
from test_debug_progress_browser import ui, page, _open_debug


def test_compute_preflight_never_claims_all_clear_on_failed_ledger(page, ui):
    page.route('**/api/account/ledger', lambda r: r.fulfill(status=503, content_type='application/json', body='{"detail":"offline"}'))
    page.route('**/api/debug/preflight', lambda r: r.fulfill(content_type='application/json', body='{"startup":{"status":"pass","checks":[{"name":"unsettled-compute-segments","status":"skip"}]}}'))
    _open_debug(page, ui)
    host = page.locator('#preflighthost')
    page.wait_for_function("() => document.querySelector('#preflighthost').textContent.includes('Live: unknown')", timeout=3000)
    assert 'no compute records are blocking' not in host.inner_text()
    host.evaluate("el => el.closest('details').open=true")
    host.scroll_into_view_if_needed()
    page.screenshot(path=evidence_path('row12-02-unknown.png'), full_page=True)
    assert page.locator('#computefix').inner_text() == ''
    page.unroute('**/api/account/ledger')
    page.route('**/api/account/ledger', lambda r: r.fulfill(content_type='application/json', body='{"segments":[],"text":"No records"}'))
    page.locator('#retryledger').click()
    page.wait_for_function("() => document.querySelector('#preflighthost').textContent.includes('Live: no compute records')")
    assert 'Live: unknown' not in host.inner_text()
