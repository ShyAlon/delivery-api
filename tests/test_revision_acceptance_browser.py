"""Hermetic private HTML only. No app/public route or live install call."""
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright


def test_private_acceptance_founder_inputs_and_mobile_pixels(tmp_path):
    html=Path(__file__).parents[1]/'scripts/private-ui/revision-acceptance.html'
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1100,'height':1050})
        page.goto(html.as_uri())
        assert page.locator('[value=approve]').is_checked() and page.locator('#edit-fields').is_hidden()
        assert page.locator('details[open]').count()==0
        page.locator('[value=edit]').check();page.locator('#continue').click()
        assert page.locator('#errors button').count()==2 and page.locator('#change').evaluate('(el)=>el===document.activeElement')
        page.locator('#change').fill('Keep My Submitted Offline Guidance')
        page.locator('#save').click();page.wait_for_function("document.querySelector('#draft-status').textContent.startsWith('Draft Saved')")
        assert page.locator('#change').input_value()=='Keep My Submitted Offline Guidance'
        page.reload();assert page.locator('#change').input_value()=='Keep My Submitted Offline Guidance'
        page.locator('[value=edit]').check();page.locator('#ack').check()
        page.evaluate('revisionPreview.failNext()');page.locator('#continue').click()
        page.wait_for_function("document.querySelector('#status').textContent.includes('Could Not Be Saved')")
        assert page.locator('#change').input_value()=='Keep My Submitted Offline Guidance' and page.locator('#continue').is_enabled()
        page.screenshot(path='/tmp/revision-acceptance-desktop.png',full_page=True)
        page.set_viewport_size({'width':390,'height':844});page.screenshot(path='/tmp/revision-acceptance-mobile.png',full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.locator('#continue').click();page.wait_for_selector('.submitted-current')
        assert page.locator('h1').inner_text()=='Review Regenerated Requirements'
        assert page.locator('[value=approve]').is_checked() and not page.locator('#ack').is_checked()
        page.locator('#ack').check();page.locator('#continue').click()
        page.wait_for_function("document.querySelector('h1').textContent==='Review Regenerated Architecture'")
        assert page.locator('.submitted-current').inner_text()=='Keep My Submitted Offline Guidance'
        assert page.locator('[value=approve]').is_checked() and not page.locator('#ack').is_checked()
        assert '4.20 Credits' in page.locator('#cost').inner_text()
        browser.close()
