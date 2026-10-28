from test_debug_progress_browser import ui, page, _open_debug


def test_debug_sections_report_unavailable_instead_of_empty(page,ui):
    page.route('**/api/tasks/dbg/execution?debug=1',lambda r:r.fulfill(status=503,content_type='application/json',body='{"detail":"private-error"}'))
    _open_debug(page,ui)
    page.locator('#debugexecution').wait_for()
    page.wait_for_function("() => document.querySelector('#debugexecution').textContent.includes('unavailable')")
    page.locator('#debugexecution details').nth(1).evaluate('el=>el.open=true')
    assert 'read_failed' in page.locator('#debugexecution').inner_text()
    assert 'private-error' not in page.locator('#debugexecution').inner_text()
    page.locator('#debugexecution details').nth(1).evaluate('el=>el.open=true')
    page.locator('#debugexecution details').nth(1).scroll_into_view_if_needed()
    page.screenshot(path='/downloads/row12-06-sections.png',full_page=True)
