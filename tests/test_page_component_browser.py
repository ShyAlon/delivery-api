"""Page owns landmarks, styles, heading shells and a single teardown lifecycle."""
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
WEB=Path(__file__).parents[1]/'src/delivery_api/web'


def test_page_frame_and_legacy_lifecycle_share_one_owner(tmp_path):
    with sync_playwright() as p:
        browser=p.chromium.launch(args=['--no-sandbox']);page=browser.new_page(viewport={'width':390,'height':740})
        def route(r):
            name=r.request.url.split('/')[-1]
            if name.endswith('.js'):r.fulfill(content_type='text/javascript',body=(WEB/name).read_text())
            else:r.fulfill(content_type='text/html',body='<link rel="stylesheet" href="/assets/style.css"><link rel="stylesheet" href="/assets/chrome.css"><div id="page-banner-content" hidden>Workspace</div><div id="page-outlet"><div data-page-intro-content hidden><h1>Review</h1></div><button>Continue</button></div>')
        page.route('http://page.test/**',route);page.goto('http://page.test/')
        page.evaluate("""async()=>{const {Page}=await import('/page-component.js');window.Page=Page;const life=await import('/page-lifecycle.js');Page.mountFrame();Page.footer(document.body);window.exits=0;life.onPageExit(()=>exits++);window.controller=life.pageController();window.at=life.pageEpoch();life.leavePage();window.stale=life.pageStale(at);document.querySelector('main').insertAdjacentHTML('afterbegin',Page.heading('<h2>Evidence</h2>'));}""")
        expect(page.locator('header')).to_have_count(1);expect(page.locator('main')).to_have_count(1);expect(page.locator('footer')).to_have_count(1)
        expect(page.locator('.page-head h2')).to_have_text('Evidence')
        assert page.evaluate('exits===1 && controller.signal.aborted && stale && Page.epoch===at+1')
        assert page.locator('[id^=ui-page-]').count()==2
        page.evaluate('Page.installStyles()');assert page.locator('[id^=ui-page-]').count()==2
        page.get_by_role('button',name='Continue').scroll_into_view_if_needed();expect(page.get_by_role('button',name='Continue')).to_be_in_viewport()
        browser.close()


def test_customer_task_heading_pixels_with_owned_page(pg_browser,tmp_path):
    from delivery_api.tenancy import tenant_context
    page,base,workspace=pg_browser
    with tenant_context(workspace.scope):task=workspace.store.create_task('Page component review','The task body remains supplied by its page.')
    page.goto(base+'/tasks/'+task['task_id']+'/view');page.locator('.detail-head h1').wait_for()
    page.screenshot(path=str(tmp_path/'page-customer-desktop.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(tmp_path/'page-customer-mobile.png'),full_page=True)
    assert page.locator('main').count()==1 and page.locator('header').count()==1
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')


def test_page_component_owns_shell_geometry_and_original_debt_is_gone():
    import json,re
    source=(WEB/'page-component.js').read_text()
    assert 'export class Page' in source
    css='\n'.join(json.loads(re.search(r'const pageStyles=(.*?);\n',source).group(1)).values())
    assert 'header{display:grid;' in css and '.page-head{' in css and '.operator-intro h1{' in css
    for name in ('style.css','chrome.css'):
        outside=(WEB/name).read_text()
        assert not re.search(r'(?:^|\})header\{|\.page-head[^{}]*\{|\.detail-head[^{}]*\{',outside)
    root=Path(__file__).parents[1]
    assert json.loads((root/'docs/ui-component-debt.json').read_text())=={}
