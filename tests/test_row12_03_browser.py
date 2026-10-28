from test_debug_progress_browser import ui, page, _open_debug


def test_operator_debug_shows_log_evidence_degradation(page, ui):
    page.route('**/api/debug/log-durability', lambda r:r.fulfill(content_type='application/json',body='{"degraded":true,"sources":[{"source":"engine-mirror","category":"OSError","since":"2026-10-27T20:00:00Z"}]}'))
    _open_debug(page, ui)
    host=page.locator('#logdurability')
    host.evaluate("el => el.closest('details').open=true")
    page.wait_for_function("() => document.querySelector('#logdurability').textContent.includes('engine-mirror')")
    host.scroll_into_view_if_needed()
    page.screenshot(path='/downloads/row12-04-log-state.png',full_page=True)
    assert 'Log evidence degraded' in host.inner_text()
