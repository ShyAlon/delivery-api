from test_debug_progress_browser import ui, page, _open_debug


def test_expiry_notice_does_not_replace_draft(page,ui):
    page.goto(ui+'/tasks/dbg/view');page.locator('.detail-head').wait_for();page.wait_for_timeout(300)
    page.evaluate("() => {const draft=document.createElement('textarea');draft.id='expiry-draft';draft.value='Unsaved answer';document.querySelector('#app').prepend(draft);window.dispatchEvent(new CustomEvent('requi:signin-needed'));window.dispatchEvent(new CustomEvent('requi:signin-needed'))}")
    assert page.locator('#signinneeded').count()==1
    assert page.locator('#expiry-draft').input_value()=='Unsaved answer'
    assert page.locator('#signinneeded a').get_attribute('target')=='_blank'
    page.screenshot(path='/downloads/row12-08-expiry.png',full_page=True)
