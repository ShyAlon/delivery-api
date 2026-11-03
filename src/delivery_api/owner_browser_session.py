"""Owner-started browser sign-in handoff. Never print or persist cookie values."""
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

class SessionUnavailable(RuntimeError):pass


def copy_cookies(client,cookies,base):
    target=urlsplit(base);copied=0
    for cookie in cookies:
        # Only the current app's access credential, never refresh/other sites.
        if (cookie.get('name')!='requi_access' or cookie.get('domain','').lstrip('.')!=target.hostname
            or cookie.get('path','/')!='/' or cookie.get('secure') and target.scheme!='https'
            or cookie.get('expires',-1)>0 and cookie['expires']<=time.time()):continue
        client.cookies.set('requi_access',cookie['value'],domain=target.hostname,path='/');copied+=1
    return copied


def load_state(client,state,base):
    if not state:return False
    path=Path(state).expanduser()
    if path.is_symlink() or not path.is_file() or path.stat().st_mode&0o077:
        raise SessionUnavailable('Saved browser session must be a private regular file (chmod 600), not a symlink')
    try:return bool(copy_cookies(client,json.loads(path.read_text()).get('cookies',[]),base))
    except (ValueError,KeyError,TypeError):raise SessionUnavailable('Saved browser session could not be read; sign in again') from None


def sign_in(client,base,*,timeout=180):
    """Dedicated visible browser. Existing ordinary browser cookies are not read."""
    from playwright.sync_api import sync_playwright
    print('Opening the verified app in a browser. Sign in there normally; no token copying. Waiting up to '+str(timeout)+'s. This sign-in may create a normal auth session, but starts no task or paid call.',flush=True)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=False)
            try:
                context=browser.new_context();page=context.new_page()
                origin=urlsplit(base)
                def restrict(route):
                    request=route.request;url=urlsplit(request.url)
                    same=(url.scheme,url.netloc)==(origin.scheme,origin.netloc)
                    allowed=request.method in ('GET','HEAD') or request.method=='POST' and url.path in ('/auth/signin','/auth/refresh','/api/client-log')
                    if same and allowed:route.continue_()
                    else:route.abort()
                page.route('**/*',restrict)
                # The app handles its normal same-origin auth. Never navigate
                # to another target or extract localStorage/refresh tokens.
                page.goto(base,wait_until='domcontentloaded')
                deadline=time.monotonic()+timeout
                while time.monotonic()<deadline:
                    if page.is_closed():raise SessionUnavailable('Sign-in window closed; acceptance not started')
                    try:
                        response=context.request.get(base+'/api/auth',timeout=5000)
                        if response.status==200 and response.json().get('authenticated') is True:
                            if copy_cookies(client,context.cookies(base),base):return
                    except Exception:pass
                    page.wait_for_timeout(1000)
                raise SessionUnavailable('Sign-in timed out; acceptance not started. Retry and sign in in the opened window')
            finally:browser.close()
    except SessionUnavailable:raise
    except Exception:raise SessionUnavailable('Could not open the sign-in browser. Install Chromium with .venv/bin/python -m playwright install chromium, then retry. No token is needed') from None
