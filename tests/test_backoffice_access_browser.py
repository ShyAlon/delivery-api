"""Access inventory failure is unknown, never a permanent invented denial."""
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
WEB=Path(__file__).parents[1]/'src/delivery_api/backoffice_web'

def test_unknown_access_is_retryable_and_does_not_claim_missing_capability(tmp_path):
    with sync_playwright() as p:
        browser=p.chromium.launch(args=['--no-sandbox']);page=browser.new_page()
        page.route('http://access.test/**',lambda route:route.fulfill(content_type='text/javascript',body=(WEB/'access.js').read_text()) if route.request.url.endswith('.js') else route.fulfill(content_type='text/html',body='<section id="active-jobs-panel"><button id="refresh">Refresh jobs</button><input></section>'))
        page.goto('http://access.test/')
        page.evaluate("""async()=>{const {accessBoundary}=await import('/access.js');let n=0;window.caps=()=>n;
        await accessBoundary(async()=>{n++;return n===1?{ok:false,status:502}:{ok:true,json:async()=>({capabilities:{'debug.read':true}})}})}""")
        expect(page.get_by_text('Access could not be checked',exact=False)).to_be_visible()
        expect(page.get_by_role('button',name='Retry access check')).to_be_enabled()
        assert 'Missing capability' not in page.locator('body').inner_text()
        page.screenshot(path=str(tmp_path/'access-unknown.png'))
        page.evaluate("document.querySelector('#refresh').disabled=false")
        expect(page.locator('#refresh')).to_be_disabled()
        page.get_by_role('button',name='Retry access check').click()
        expect(page.locator('#refresh')).to_be_enabled()
        expect(page.locator('input')).to_be_enabled()
        assert page.evaluate('caps()')==2
        browser.close()
