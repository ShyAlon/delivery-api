from test_debug_progress_browser import ui, page, _open_debug


def test_cached_preflight_stale_write_status_visible(page,ui):
    page.route('**/api/debug/preflight',lambda r:r.fulfill(content_type='application/json',body='{"startup":{"status":"pass","generated_at":"old","checks":[]},"persistence":{"availability":"stale","write_status":"failed","expected_generated_at":"now"}}'))
    _open_debug(page,ui)
    host=page.locator('#preflighthost');host.evaluate("el=>el.closest('details').open=true")
    page.wait_for_function("()=>document.querySelector('#preflighthost').textContent.includes('Cached report: stale')")
    host.scroll_into_view_if_needed();page.screenshot(path='/downloads/row12-14-cache.png',full_page=True)
    assert 'stdout is authoritative' in host.inner_text()
