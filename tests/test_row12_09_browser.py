from test_artifacts import evidence_path
from test_debug_progress_browser import ui, page, _open_debug


def test_supplemental_validation_status_is_visible(page,ui):
    page.goto(ui+'/tasks/'+page._debug_task_id+'/view');page.locator('.detail-head').wait_for();page.wait_for_timeout(300)
    page.evaluate('''async()=>{const {validationPanelHtml}=await import('/assets/validation-panel.js');const host=document.createElement('div');host.innerHTML=validationPanelHtml({groups:[],supplemental_availability:{needs:{availability:'unavailable',reason:'invalid_or_unreadable'}}});document.querySelector('#app').prepend(host)}''')
    assert 'Primary validation artifact preserved' in page.locator('.supplemental-status').inner_text()
    page.screenshot(path=evidence_path('row12-11-provenance.png'),full_page=True)
