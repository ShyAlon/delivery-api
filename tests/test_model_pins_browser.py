import os
from pathlib import Path
_ARTIFACTS=Path(os.environ.get('REQUI_TEST_ARTIFACTS','/downloads'));_ARTIFACTS.mkdir(parents=True,exist_ok=True)
"""Real shared Back office UI with fake external service responses, no accounts or provider."""
import json,socket,threading,time
from pathlib import Path
import httpx,uvicorn
from delivery_api.backoffice import create_app
from test_model_pins import payload


def test_model_pin_review_cancel_edit_menu_and_mobile():
    from playwright.sync_api import sync_playwright,expect
    value=payload();value['row']['model']='deepseek-flash';value['context']['model']='deepseek-flash';calls=[]
    def upstream(request):
        calls.append(request)
        if request.url.path=='/api/operator/backoffice-access':return httpx.Response(200,json={'capabilities':{c:True for c in ('services.operate','logs.read','debug.read','sessions.read','analytics.read','usage.read','payments.read')}})
        if request.url.path=='/api/operator/model-pins':return httpx.Response(200,json={'pins':[{'payload':value,'revision':'abc','is_default':True,'status':{'fetched_at':'2026-11-01T11:00:00Z','badge':'Stale - refresh needed'}}],'history':[],'bootstrap':[]})
        if request.url.path.endswith('/refresh') or request.url.path.endswith('/edit'):
            return httpx.Response(200,json={'before':value,'review':{'payload':value,'expected':'abc','subject':'fake','expires':9999999999},'signature':'fake'})
        return httpx.Response(403,json={'detail':'unavailable'})
    app=create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream))
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,lifespan='off',log_level='error'));thread=threading.Thread(target=server.run);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.02)
        with sync_playwright() as p:
            browser=p.chromium.launch(args=['--no-sandbox']);page=browser.new_page(viewport={'width':1440,'height':1000},extra_http_headers={'Authorization':'Bearer fake'});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{port}/#model-pins');expect(page.locator('#model-pins-panel tbody tr')).to_have_count(1)
            page.locator('#model-pins-panel .row-menu-trigger').click();page.get_by_role('menuitem',name='Refresh prices').click();expect(page.get_by_role('dialog')).to_be_visible();expect(page.get_by_role('button',name='Approve default')).to_be_focused()
            page.screenshot(path=str(_ARTIFACTS/'model-pins-review.png'),full_page=False)
            page.get_by_role('button',name='Cancel',exact=True).click();assert not any(r.url.path.endswith('/approve') for r in calls)
            page.locator('#model-pins-panel .row-menu-trigger').click();page.get_by_role('menuitem',name='Edit pin').click();expect(page.get_by_label('Public model pin JSON')).to_be_visible();page.get_by_role('button',name='Cancel',exact=True).click()
            page.screenshot(path=str(_ARTIFACTS/'model-pins-desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(_ARTIFACTS/'model-pins-mobile.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth');assert not errors;browser.close()
    finally:server.should_exit=True;thread.join(10)


def test_missing_operator_capability_disables_new_model_and_names_gap(tmp_path):
 from playwright.sync_api import sync_playwright,expect
 def upstream(request):
  if request.url.path=='/api/operator/backoffice-access':return httpx.Response(200,json={'capabilities':{'services.operate':False}})
  return httpx.Response(403,json={'detail':'unavailable'})
 app=create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream))
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,lifespan='off',log_level='error'));thread=threading.Thread(target=server.run);thread.start()
 try:
  for _ in range(100):
   if server.started:break
   time.sleep(.02)
  with sync_playwright() as p:
   browser=p.chromium.launch(args=['--no-sandbox']);page=browser.new_page(viewport={'width':1280,'height':900},extra_http_headers={'Authorization':'Bearer fake'})
   page.goto(f'http://127.0.0.1:{port}/#model-pins')
   expect(page.get_by_role('button',name='Add model',exact=True)).to_be_disabled()
   expect(page.locator('#model-pins-panel .access-denied')).to_contain_text('services.operate')
   page.screenshot(path=str(tmp_path/'backoffice-missing-capability.png'))
   browser.close()
 finally:server.should_exit=True;thread.join(10)


def test_shared_add_model_labels_focus_enter_inline_error_and_escape(tmp_path):
 from playwright.sync_api import sync_playwright,expect
 calls=[]
 def upstream(request):
  calls.append(request)
  if request.url.path=='/api/operator/backoffice-access':return httpx.Response(200,json={'capabilities':{'services.operate':True}})
  if request.url.path=='/api/operator/model-pins':return httpx.Response(200,json={'pins':[],'history':[],'bootstrap':[]})
  return httpx.Response(403,json={'detail':'Review unavailable: fixture refusal'})
 app=create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(upstream))
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,lifespan='off',log_level='error'));thread=threading.Thread(target=server.run);thread.start()
 try:
  for _ in range(100):
   if server.started:break
   time.sleep(.02)
  with sync_playwright() as p:
   browser=p.chromium.launch(args=['--no-sandbox']);page=browser.new_page(viewport={'width':1280,'height':900},extra_http_headers={'Authorization':'Bearer fake'})
   page.goto(f'http://127.0.0.1:{port}/#model-pins');page.get_by_role('button',name='Add model',exact=True).click()
   d=page.get_by_role('dialog',name='Add reviewed model');expect(d).to_be_visible()
   for label in ('Provider','Provider model ID','Context tokens','Maximum output tokens'):expect(d.get_by_label(label,exact=True)).to_be_visible()
   d.get_by_label('Provider model ID',exact=True).focus();page.keyboard.press('Enter')
   expect(d.locator('.dialog-status')).to_contain_text('fixture refusal')
   assert sum(r.url.path.endswith('/new') for r in calls)==1
   d.get_by_role('button',name='Fetch prices and review').focus();page.keyboard.press('Tab');expect(d.get_by_label('Provider',exact=True)).to_be_focused()
   page.screenshot(path=str(tmp_path/'shared-add-model-desktop.png'))
   page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(tmp_path/'shared-add-model-mobile.png'))
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
   page.keyboard.press('Escape');expect(d).not_to_be_visible();browser.close()
 finally:server.should_exit=True;thread.join(10)
