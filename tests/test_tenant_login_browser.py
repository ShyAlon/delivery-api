"""Hermetic cloud login shell, no Supabase/GitHub requests."""
from pathlib import Path
import pytest
pytest.importorskip('playwright.sync_api')
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).parents[1]/'src/delivery_api/web'

@pytest.mark.parametrize('width,height',[(1366,768),(390,844)])
def test_cloud_login_private_copy_no_local_availability_probe(width,height,tmp_path):
    seen=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':width,'height':height})
        def route_handler(route):
            url=route.request.url;seen.append(url)
            if '/api/auth' in url:
                route.fulfill(json={'mode':'supabase','authenticated':False,'actor':'anonymous','admin':False,'providers':[]});return
            if '/health' in url:route.fulfill(json={'ok':True});return
            if '/auth/signup' in url:route.fulfill(json={'status':'confirmation_required'});return
            if '/api/' in url:route.fulfill(json={});return
            file='index.html' if url.rstrip('/')=='http://preview.local' else url.split('/assets/')[-1]
            path=ROOT/file
            if path.is_file():route.fulfill(path=str(path));return
            route.fulfill(status=404,body='missing')
        page.route('**/*',route_handler)
        page.goto('http://preview.local/')
        expect(page.locator('#authform')).to_be_visible()
        expect(page.locator('.auth-copy')).to_contain_text('private workspace')
        assert 'Accounts share' not in page.locator('.auth-copy').inner_text()
        button=page.locator('.auth-submit').bounding_box()
        assert button['y']+button['height']<=height
        page.get_by_role('button',name='Create account',exact=True).click()
        page.locator('input[name=username]').fill('test@example.invalid')
        page.locator('input[name=password]').fill('Fake-test-password-1')
        page.locator('.auth-submit').click()
        expect(page.locator('#authmessage')).to_contain_text('Check your email')
        assert not any('/auth/email-availability' in url for url in seen)
        page.screenshot(path=str(tmp_path/f'cloud-login-{width}.png'))
        browser.close()
