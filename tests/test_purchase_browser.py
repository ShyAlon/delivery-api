"""Actual shipped ES module, controlled local fixture boundary, no payment network."""
import tempfile
from pathlib import Path as _ScreenshotPath
SCREENSHOTS = _ScreenshotPath(tempfile.mkdtemp(prefix='delivery-browser-evidence-'))
import functools,http.server,json,threading
from contextlib import contextmanager
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).parents[1]

@contextmanager
def serving():
 handler=functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(ROOT/'src/delivery_api/web'))
 server=http.server.ThreadingHTTPServer(('127.0.0.1',0),handler)
 thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 try:yield f'http://127.0.0.1:{server.server_port}'
 finally:server.shutdown();thread.join();server.server_close()

FIXTURE="""<link rel='stylesheet' href='/purchase.css'><header>Requi.re</header><main id='app'></main><script type='module'>
import {createPurchase} from '/purchase.js';
window.calls={create:0,status:0,receipt:0};window.fixtureState='pending';
const values={'credits-5':[5,500],'credits-12':[12,1150],'credits-25':[25,2300],'credits-50':[50,4500]};
let offer='credits-50';window.releaseCreate=null;
const boundary={account:async()=>({checkout_available:true,balance_credits:8.5}),
 create:async x=>{calls.create++;offer=x.offer_id;await new Promise(resolve=>window.releaseCreate=resolve);return {purchase_id:'fixture-purchase',offer_id:offer}},
 status:async id=>{calls.status++;const [credits,amount_minor]=values[new URLSearchParams(location.search).get('offer')||offer];return {purchase_id:id,offer_id:new URLSearchParams(location.search).get('offer')||offer,credits,amount_minor,currency:'USD',state:fixtureState,credit_posted:fixtureState==='success',balance_credits:58.5}},
 receipt:async id=>{calls.receipt++;return {purchase_id:id,credit_posted:true,credits:50,amount_minor:4500,currency:'USD'}}};
window.purchase=createPurchase({showError:e=>document.querySelector('#app').textContent=e.message},{boundary});purchase.purchasePage();
</script>"""

@pytest.mark.parametrize('width',[320,390,1280])
def test_all_checkout_states_and_actual_layout(tmp_path,width):
 with serving() as origin,sync_playwright() as p:
  browser=p.chromium.launch(headless=True);page=browser.new_page(viewport={'width':width,'height':844})
  page.route('**/credits**',lambda r:r.fulfill(body=FIXTURE,content_type='text/html'))
  page.route('**/assets/credit-offers.json',lambda r:r.fulfill(body=(ROOT/'src/delivery_api/web/credit-offers.json').read_text(),content_type='application/json'))
  page.route('**/api/client-log',lambda r:r.fulfill(body='{}',content_type='application/json'))
  page.goto(origin+'/credits?view=choose&offer=credits-50');page.wait_for_selector('.purchase-offer')
  assert page.locator('.purchase-discount').all_text_contents()==['4% off!','8% off!','10% off!']
  page.wait_for_timeout(240);page.screenshot(path=str(SCREENSHOTS/f'checkout-choose-{width}.png'),full_page=True)
  page.locator('[data-review]').click();assert page.locator('h1').inner_text()=='Review Purchase'
  page.reload();page.wait_for_selector('[data-pay]');assert '$45.00 USD' in page.locator('[data-pay]').inner_text()
  page.locator('[data-pay]').click();page.wait_for_selector('[aria-busy=true]');page.screenshot(path=str(SCREENSHOTS/f'checkout-busy-{width}.png'),full_page=True)
  assert page.locator('button').is_disabled();assert page.evaluate('calls.create')==1
  page.evaluate('releaseCreate()');page.wait_for_selector('[data-check]');assert page.locator('h1').inner_text()=='Payment Pending'
  assert "Don't pay again" in page.locator('#app').inner_text()
  for state in ('pending','cancelled','declined','unknown','success'):
   page.evaluate('(s)=>window.fixtureState=s',state);page.evaluate('purchase.purchasePage()');page.wait_for_function('(s)=>document.title.includes({pending:"Payment Pending",cancelled:"Checkout Cancelled",declined:"Payment Not Completed",unknown:"Payment Needs Review",success:"Credits Added"}[s])',arg=state)
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
   page.wait_for_timeout(240);page.screenshot(path=str(SCREENSHOTS/f'checkout-{state}-{width}.png'),full_page=True)
  with page.expect_download() as download:page.locator('[data-receipt]').click()
  assert download.value.suggested_filename=='requi-receipt.txt'
  assert page.locator('a[href="/"]').inner_text()=='Continue to Tasks'
  assert page.evaluate('calls.create')==1
  browser.close()

def test_closed_boundary_never_creates_payment_or_spoofs_success():
 with serving() as origin,sync_playwright() as p:
  browser=p.chromium.launch(headless=True);page=browser.new_page()
  closed=FIXTURE[:FIXTURE.index('<script')]+"<script type='module'>import {createPurchase} from '/purchase.js';createPurchase({showError:console.error}).purchasePage()</script>"
  page.route('**/credits**',lambda r:r.fulfill(body=closed,content_type='text/html'))
  page.route('**/assets/credit-offers.json',lambda r:r.fulfill(body=(ROOT/'src/delivery_api/web/credit-offers.json').read_text(),content_type='application/json'))
  page.route('**/api/client-log',lambda r:r.fulfill(body='{}',content_type='application/json'))
  payments=[];page.on('request',lambda r:payments.append(r.url) if '/api/payments/' in r.url else None)
  page.goto(origin+'/credits?view=success&offer=credits-50');page.wait_for_selector('.purchase-offer')
  assert page.locator('h1').inner_text()=='Add Credits' and 'Unavailable' in page.locator('.purchase-balance').inner_text()
  page.locator('[data-review]').click();assert page.locator('[data-pay]').is_disabled()
  assert 'Payments have not been enabled' in page.locator('#app').inner_text();assert not payments
  page.wait_for_timeout(240);page.screenshot(path=str(SCREENSHOTS/'checkout-closed-boundary.png'),full_page=True)
  browser.close()

@pytest.mark.parametrize('width',[320,390,1249])
def test_actual_workspace_shell_identity_and_checkout_entry(width):
 with serving() as origin,sync_playwright() as p:
  browser=p.chromium.launch(headless=True);page=browser.new_page(viewport={'width':width,'height':844})
  # Route the actual application shell/assets, with only the authenticated
  # service responses stubbed. The page uses the closed shipped boundary.
  page.route('**/assets/**',lambda r:r.fulfill(path=str(ROOT/'src/delivery_api/web'/r.request.url.split('/assets/')[1].split('?')[0])))
  page.route('**/credits**',lambda r:r.fulfill(path=str(ROOT/'src/delivery_api/web/index.html')))
  page.route('**/api/auth',lambda r:r.fulfill(json={'mode':'local','authenticated':True,'actor':'Test Customer','admin':False,'providers':[]}))
  page.route('**/health',lambda r:r.fulfill(json={'runtime_id':'hermetic-ui','repositories':{}}))
  page.route('**/api/client-log',lambda r:r.fulfill(json={}))
  page.goto(origin+'/credits?view=review&offer=credits-12');page.wait_for_selector('[data-pay]')
  assert page.locator('[data-pay]').is_disabled()
  assert page.locator('header a[href="/credits"]').inner_text()=='Add Credits'
  assert page.locator('.purchase-balance').inner_text()=='Balance Unavailable'
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  page.wait_for_timeout(240);page.screenshot(path=str(SCREENSHOTS/f'checkout-shell-{width}.png'),full_page=True)
  browser.close()

def test_unknown_outcome_blocks_new_purchase_after_back_and_refresh():
 with serving() as origin,sync_playwright() as p:
  browser=p.chromium.launch(headless=True);page=browser.new_page()
  source=FIXTURE.replace("return {purchase_id:'fixture-purchase',offer_id:offer}","throw new Error('unknown create outcome')")
  page.route('**/credits**',lambda r:r.fulfill(body=source,content_type='text/html'))
  page.route('**/assets/credit-offers.json',lambda r:r.fulfill(body=(ROOT/'src/delivery_api/web/credit-offers.json').read_text(),content_type='application/json'))
  page.route('**/api/client-log',lambda r:r.fulfill(json={}))
  page.goto(origin+'/credits?view=review&offer=credits-5');page.wait_for_selector('[data-pay]');page.locator('[data-pay]').click()
  page.wait_for_selector('[aria-busy=true]');page.evaluate('releaseCreate()');page.wait_for_selector('[data-help]')
  page.goto(origin+'/credits?view=review&offer=credits-5');page.wait_for_selector('[data-pay]')
  assert page.locator('[data-pay]').is_disabled() and 'previous checkout needs review' in page.locator('#app').inner_text()
  browser.close()

@pytest.mark.parametrize('tamper',["purchase_id:'foreign-purchase'","amount_minor:499","credit_posted:false"])
def test_unverified_success_is_never_displayed(tamper):
 with serving() as origin,sync_playwright() as p:
  browser=p.chromium.launch(headless=True);page=browser.new_page()
  source=FIXTURE.replace("state:fixtureState,credit_posted:fixtureState==='success',balance_credits:58.5",f"state:'success',credit_posted:true,balance_credits:58.5,{tamper}")
  page.route('**/credits**',lambda r:r.fulfill(body=source,content_type='text/html'))
  page.route('**/assets/credit-offers.json',lambda r:r.fulfill(body=(ROOT/'src/delivery_api/web/credit-offers.json').read_text(),content_type='application/json'))
  page.route('**/api/client-log',lambda r:r.fulfill(json={}))
  page.goto(origin+'/credits?view=success&offer=credits-50&purchase=fixture-purchase');page.wait_for_selector('[data-help]')
  assert page.locator('h1').inner_text()=='Payment Needs Review'
  assert '58.5' not in page.locator('.purchase-balance').inner_text() and not page.locator('[data-receipt]').count()
  browser.close()


def test_receipt_uses_shared_guard_and_blocks_programmatic_double_click():
 with serving() as origin,sync_playwright() as p:
  browser=p.chromium.launch(headless=True);page=browser.new_page()
  source=FIXTURE.replace("window.fixtureState='pending'","window.fixtureState='success'").replace("calls.receipt++;return", "calls.receipt++;await new Promise(resolve=>window.releaseReceipt=resolve);return")
  page.route('**/credits**',lambda r:r.fulfill(body=source,content_type='text/html'))
  page.route('**/assets/credit-offers.json',lambda r:r.fulfill(body=(ROOT/'src/delivery_api/web/credit-offers.json').read_text(),content_type='application/json'))
  page.route('**/api/client-log',lambda r:r.fulfill(json={}))
  page.goto(origin+'/credits?view=success&offer=credits-50&purchase=fixture-purchase');page.wait_for_selector('[data-receipt]')
  page.locator('[data-receipt]').click()
  page.wait_for_function('calls.receipt===1')
  assert page.locator('[data-receipt]').get_attribute('aria-busy')=='true'
  page.locator('[data-receipt]').dispatch_event('click')
  assert page.evaluate('calls.receipt')==1
  page.evaluate('releaseReceipt()');page.wait_for_function("!document.querySelector('[data-receipt]').disabled")
  assert page.locator('[data-receipt]').get_attribute('aria-busy') is None
  browser.close()
