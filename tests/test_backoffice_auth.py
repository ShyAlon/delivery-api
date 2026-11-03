"""Hermetic browser auth bridge. Never contacts owner preview or paid tasks."""
import httpx
from fastapi.testclient import TestClient
from delivery_api.backoffice import create_app


def test_auth_bridge_is_narrow_same_origin_and_keeps_httponly_cookies():
 calls=[]
 def service(r):
  calls.append(r)
  if r.url.path=='/auth/signin':
   return httpx.Response(200,json={'status':'signed_in'},headers=[('Set-Cookie','requi_access=opaque; HttpOnly; Path=/; SameSite=Lax'),('Set-Cookie','requi_refresh=refresh; HttpOnly; Path=/auth; SameSite=Lax')])
  if r.url.path=='/auth/logout':return httpx.Response(200,json={'status':'signed_out'},headers={'Set-Cookie':'requi_access=""; Max-Age=0; Path=/'})
  return httpx.Response(200,json={'authenticated':'requi_access=opaque' in r.headers.get('cookie',''),'mode':'supabase','actor':'Owner'})
 with TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(service))) as c:
  assert c.get('/api/auth').json()['authenticated'] is False
  assert c.post('/auth/signin',json={}).status_code==403
  assert c.post('/auth/signin',headers={'Origin':'https://evil.invalid'},json={}).status_code==403
  assert c.post('/auth/arbitrary',headers={'Origin':'http://testserver'},json={}).status_code==404
  r=c.post('/auth/signin',headers={'Origin':'http://testserver'},json={'username':'owner@example.com','password':'fixture only'})
  assert r.status_code==200 and len(r.headers.get_list('set-cookie'))==2
  assert all('HttpOnly' in x for x in r.headers.get_list('set-cookie'))
  assert calls[-1].headers['origin']=='http://127.0.0.1:8000'
  assert c.get('/api/auth').json()['authenticated'] is True
  c.post('/auth/refresh',headers={'Origin':'http://testserver'},json={})
  assert 'requi_refresh=refresh' in calls[-1].headers['cookie']
  c.post('/auth/logout',headers={'Origin':'http://testserver'},json={})
  assert c.get('/api/auth').json()['authenticated'] is False
  assert c.get('/api/auth?destination=evil').status_code==422
  assert c.get('/auth/signin').status_code==404
  assert c.get('/api/auth',headers={'Sec-Fetch-Site':'cross-site'}).status_code==403


def test_auth_refusal_cannot_become_authenticated_or_forward_redirect():
 for status,body in [(401,{'detail':'wrong password'}),(302,{'redirect':'elsewhere'}),(200,[])]:
  c=TestClient(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(lambda r:httpx.Response(status,json=body))))
  r=c.get('/api/auth')
  assert r.status_code==(401 if status==401 else 502)
  assert r.headers['cache-control']=='no-store'


def test_browser_signin_avatar_logout_and_expiry_never_replays_write(tmp_path):
 import socket,threading,time,uvicorn,json
 from playwright.sync_api import sync_playwright,expect
 calls=[]
 def service(r):
  calls.append(r);signed='requi_access=opaque' in r.headers.get('cookie','')
  if r.url.path=='/health':return httpx.Response(200,json={'repositories':{'delivery-api':'a'*40,'interview-engine':'b'*40}})
  if r.url.path=='/api/auth':return httpx.Response(200,json={'authenticated':signed,'mode':'supabase','actor':'owner@example.com'})
  if r.url.path=='/auth/signin':return httpx.Response(200,json={'status':'signed_in'},headers={'Set-Cookie':'requi_access=opaque; HttpOnly; Path=/; SameSite=Lax'})
  if r.url.path=='/auth/logout':return httpx.Response(200,json={'status':'signed_out'},headers={'Set-Cookie':'requi_access=""; Max-Age=0; Path=/'})
  if r.url.path=='/api/operator/backoffice-access':return httpx.Response(200,json={'capabilities':{'debug.read':True,'logs.read':True}})
  if r.url.path=='/api/operations/active-jobs':return httpx.Response(200,json={'scope':'current_authorized_workspace','mutations_available':False,'jobs':[{'title':'Fixture task','task_id':'fixture','job_id':'job','kind':'phase:intent','state':'running','task_state':'running','generation':1,'task_generation':1,'updated_at':1}],'truncated':False})
  if r.url.path.endswith('/stop'):return httpx.Response(401,json={'detail':'Sign in again'},headers={'Set-Cookie':'requi_access=""; Max-Age=0; Path=/'})
  return httpx.Response(403,json={'detail':'Unavailable'})
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 server=uvicorn.Server(uvicorn.Config(create_app(service_url='http://127.0.0.1:8000',transport=httpx.MockTransport(service)),host='127.0.0.1',port=port,lifespan='off',log_level='error'))
 thread=threading.Thread(target=server.run);thread.start()
 try:
  for _ in range(100):
   if server.started:break
   time.sleep(.02)
  with sync_playwright() as p:
   browser=p.chromium.launch(args=['--no-sandbox']);page=browser.new_page(viewport={'width':1280,'height':950})
   errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(f'http://127.0.0.1:{port}')
   expect(page.locator('#authform')).to_be_visible();expect(page.locator('#backoffice-workspace')).to_be_hidden()
   assert not any('/api/operations/' in r.url.path for r in calls)
   page.screenshot(path='/downloads/backoffice-shared-signin-desktop.png',full_page=True)
   page.locator('[name=username]').fill('owner@example.com');page.locator('[name=password]').fill('fixture password')
   page.get_by_role('button',name='Sign in',exact=True).last.click()
   expect(page.locator('#accountbutton')).to_be_visible();expect(page.locator('#active-jobs tbody tr')).to_have_count(1)
   page.locator('#accountbutton').click();expect(page.get_by_role('menuitem',name='Sign out')).to_be_visible()
   page.screenshot(path='/downloads/backoffice-shared-avatar-desktop.png',full_page=True)
   page.keyboard.press('Escape');expect(page.locator('#accountbutton')).to_be_focused()
   page.get_by_role('button',name='Job actions',exact=True).click();page.get_by_role('menuitem',name='Delete',exact=True).click()
   page.get_by_role('button',name='Stop and delete',exact=True).click()
   expect(page.locator('#authform')).to_be_visible();expect(page.locator('#backoffice-workspace')).to_be_hidden()
   assert len([r for r in calls if r.url.path.endswith('/stop')])==1
   page.locator('[name=username]').fill('owner@example.com');page.locator('[name=password]').fill('fixture password')
   page.get_by_role('button',name='Sign in',exact=True).last.click();expect(page.locator('#accountbutton')).to_be_visible()
   assert len([r for r in calls if r.url.path.endswith('/stop')])==1
   page.set_viewport_size({'width':390,'height':844});page.locator('#accountbutton').click()
   page.screenshot(path='/downloads/backoffice-shared-avatar-phone.png',full_page=True)
   page.get_by_role('menuitem',name='Sign out').click();expect(page.locator('#authform')).to_be_visible()
   page.screenshot(path='/downloads/backoffice-shared-signin-phone.png',full_page=True)
   assert page.locator('.auth-card input').first.evaluate("e=>getComputedStyle(e).padding")=="14px"
   assert page.locator('.auth-card').bounding_box()['x']==20
   assert page.locator('.auth-copy').bounding_box()['x']==20
   assert not errors
   browser.close()
 finally:server.should_exit=True;thread.join(timeout=10)
