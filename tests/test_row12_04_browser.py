from test_debug_progress_browser import ui, page, _open_debug


def test_local_incomplete_usage_is_not_a_complete_cost(page, ui):
    page.route('**/api/tasks/dbg/cost', lambda r:r.fulfill(content_type='application/json',body='{"price":1,"price_usd":1,"evidence":{"complete":false,"rejected_rows":2}}'))
    page.goto(ui+'/tasks/dbg/view')
    page.wait_for_function("() => document.querySelector('#sessioncost').textContent==='Session Cost Incomplete'")
    assert '2 rejected rows' in page.locator('#sessioncost').get_attribute('title')
    page.screenshot(path='/downloads/row12-10-cost-incomplete.png',full_page=True)
