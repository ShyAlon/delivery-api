import json,time
import httpx,pytest
from delivery_api.owner_browser_session import copy_cookies,load_state,SessionUnavailable
BASE='http://127.0.0.1:8001'

def cookie(**extra):return {'name':'requi_access','value':'private-cookie','domain':'127.0.0.1','path':'/','expires':time.time()+300,**extra}

def test_only_current_host_live_access_cookie_is_copied():
    with httpx.Client() as client:
        assert copy_cookies(client,[cookie(),cookie(domain='another.test'),cookie(name='requi_refresh'),cookie(path='/auth'),cookie(expires=1),cookie(secure=True)],BASE)==1
        assert [(c.name,c.value) for c in client.cookies.jar]==[('requi_access','private-cookie')]

def test_private_saved_state_reuses_session_without_browser(tmp_path):
    path=tmp_path/'session.json';path.write_text(json.dumps({'cookies':[cookie()],'origins':[{'localStorage':[{'value':'do-not-read'}]}]}));path.chmod(0o600)
    with httpx.Client() as client:assert load_state(client,path,BASE)
    path.chmod(0o644)
    with httpx.Client() as client,pytest.raises(SessionUnavailable,match='private'):load_state(client,path,BASE)

def test_expired_or_other_origin_state_does_not_authenticate(tmp_path):
    path=tmp_path/'session.json';path.write_text(json.dumps({'cookies':[cookie(domain='other.test'),cookie(expires=1)]}));path.chmod(0o600)
    with httpx.Client() as client:assert not load_state(client,path,BASE) and not client.cookies


def test_visible_handoff_uses_httponly_cookie_without_token_prompt(monkeypatch,capsys):
    import socket,threading
    from http.server import BaseHTTPRequestHandler,HTTPServer
    from delivery_api import owner_browser_session as module
    from playwright.sync_api import sync_playwright
    mutations=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path=='/api/auth':
                body=json.dumps({'authenticated':'requi_access=browser-private-cookie' in self.headers.get('Cookie','')}).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
            else:
                self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Set-Cookie','requi_access=browser-private-cookie; HttpOnly; Path=/');self.end_headers();self.wfile.write(b'<h1>Signed in fixture</h1>')
        def do_POST(self):mutations.append(self.path);self.send_response(500);self.end_headers()
    server=HTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever);thread.start()
    # Exercise real browser cookies with headless fixture; production remains visible.
    with sync_playwright() as p:
        launch=p.chromium.launch
        class Chromium:
            def launch(self,**kw):return launch(headless=True,args=['--no-sandbox'])
        class PW:
            chromium=Chromium()
            def __enter__(self):return self
            def __exit__(self,*a):pass
        monkeypatch.setattr('playwright.sync_api.sync_playwright',lambda:PW())
        try:
            base='http://127.0.0.1:'+str(server.server_port)
            with httpx.Client() as client:
                module.sign_in(client,base,timeout=5)
                assert client.cookies.get('requi_access')=='browser-private-cookie'
        finally:server.shutdown();thread.join();server.server_close()
    assert not mutations
    assert 'browser-private-cookie' not in capsys.readouterr().out
