from test_debug_progress_browser import ui, page, _open_debug


def test_result_freshness_notice_is_readable_and_keeps_content(page,ui):
    page.goto(ui+'/tasks/dbg/view');page.locator('.detail-head').wait_for();page.wait_for_timeout(300)
    page.evaluate('''async()=>{const {refreshResult}=await import('/assets/result-freshness.js');const slot=document.createElement('div');slot.id='test-results';slot.textContent='Previous saved results';document.querySelector('#app').prepend(slot);await refreshResult(slot,async()=>true);try{await refreshResult(slot,async()=>{throw Error('offline')})}catch{}}''')
    assert 'Previous saved results' in page.locator('#test-results').inner_text()
    assert 'Phase results stale' in page.locator('.result-stale').inner_text()
    page.locator('.result-stale').scroll_into_view_if_needed()
    page.wait_for_timeout(100)
    page.screenshot(path='/downloads/row12-07-stale.png',full_page=True)
