"""Remember the editable email only, never password or authentication tokens."""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright
from test_ui_playwright import _port


def test_signin_email_prefills_overwrites_clears_and_enter_submits(tmp_path):
    port=_port();base=f'http://127.0.0.1:{port}'
    env={**os.environ,'DELIVERY_DATABASE_URL':f"sqlite:///{tmp_path/'remember.db'}",
         'DELIVERY_BASIC_AUTH_ENABLED':'1','DELIVERY_COOKIE_SECURE':'0',
         'PYTHONPATH':str(Path(__file__).parents[1]/'src')}
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','delivery_api.app:app','--host','127.0.0.1','--port',str(port)],env=env)
    try:
        for _ in range(60):
            try:urllib.request.urlopen(base+'/health',timeout=.2);break
            except Exception:time.sleep(.1)
        with sync_playwright() as p:
            browser=p.chromium.launch();context=browser.new_context();page=context.new_page()
            page.goto(base);email=page.locator('[name=username]');password=page.locator('[name=password]')
            email.wait_for();assert email.input_value()==''
            for address in ['first@example.test','replacement@example.test']:
                email.fill(address);password.fill('Remember fake 42!')
                # Failed submitted sign-ins also remember an editable email, never the password.
                password.press('Enter');page.locator('#authmessage .notice').wait_for()
                page.reload();email.wait_for()
                assert email.input_value()==address
                assert password.input_value()==''
                stored=page.evaluate('Object.fromEntries(Object.entries(localStorage))')
                assert stored['requi-re.signin-email']==address
                assert 'Remember fake 42!' not in str(stored)
                assert not any(word in key.lower() for key in stored for word in ('password','token'))
            page.screenshot(path='/downloads/remember-signin-email.png',full_page=True)
            email.fill('');page.reload();email.wait_for();assert email.input_value()==''
            assert page.evaluate("localStorage.getItem('requi-re.signin-email')") is None
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path='/downloads/remember-signin-email-mobile.png',full_page=True)
            # Private/blocked storage must never prevent authentication submission.
            page.add_init_script("Storage.prototype.getItem=Storage.prototype.setItem=Storage.prototype.removeItem=()=>{throw new Error('blocked storage')}")
            page.reload();email.wait_for();email.fill('blocked@example.test')
            password.fill('Remember fake 42!');password.press('Enter')
            page.locator('#authmessage .notice').wait_for()
            browser.close()
    finally:
        proc.terminate()
        try:proc.wait(timeout=5)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
