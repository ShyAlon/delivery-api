from test_debug_progress_browser import ui, page, _open_debug


def test_model_list_unavailable_notice_keeps_pinned_route(page,ui):
    page.route('**/api/model-routes',lambda r:r.fulfill(content_type='application/json',body='{"routes":[{"id":"ollama","available":true,"model":"ollama:pinned","models":["pinned"],"model_list":{"availability":"unavailable"}}]}'))
    page.goto(ui+'/tasks/dbg/view');page.locator('#modellistunavailable').wait_for()
    assert 'pinned model remains available' in page.locator('#modellistunavailable').inner_text()
    page.screenshot(path='/downloads/row12-12-models.png',full_page=True)
