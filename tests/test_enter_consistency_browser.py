"""Shared Enter resolution, IME/multiline rules and in-flight dispatch safety."""
from playwright.sync_api import expect


def test_signin_tab_password_enter_and_repeated_submit_dispatch_once(pg_browser):
    page,base,_=pg_browser;page.context.clear_cookies()
    calls=[]
    def signin(route):
        calls.append(route.request.post_data)
        route.fulfill(status=401,json={'detail':'Fixture refusal'})
    page.route('**/auth/signin',signin)
    page.goto(base);page.locator('[name=username]').fill('fixture@example.test')
    page.keyboard.press('Tab');expect(page.locator('[name=password]')).to_be_focused()
    page.keyboard.type('Fixture secret not logged 42!')
    # Two submit events in one turn reproduce programmatic/keyboard reentry while fetch is in flight.
    page.evaluate("()=>{const f=document.querySelector('#authform');f.requestSubmit();f.requestSubmit()}")
    page.locator('#authmessage .notice').wait_for()
    assert len(calls)==1
    page.locator('[name=password]').press('Enter');page.wait_for_function("document.querySelector('.auth-submit').disabled===false")
    assert len(calls)==2


def test_dialog_enter_preserves_multiline_composition_and_disabled_submit(pg_browser):
    page,base,_=pg_browser;page.goto(base)
    page.evaluate('''async()=>{const {Dialog}=await import('/assets/shared-dialog.js');
     const dialog=Dialog.open(document.querySelector('#modal'),Dialog.html('<form><input id="entry"><textarea id="multiline"></textarea><button class="primary" type="submit" disabled>Send</button></form>'));
     window.submits=0;dialog.querySelector('form').onsubmit=e=>{e.preventDefault();window.submits++};
    }''')
    page.locator('#entry').press('Enter');assert page.evaluate('submits')==0
    page.locator('button.primary[type=submit]').evaluate('e=>e.disabled=false')
    page.locator('#entry').dispatch_event('keydown',{'key':'Enter','isComposing':True});assert page.evaluate('submits')==0
    page.locator('#multiline').fill('first');page.locator('#multiline').press('Enter');assert page.locator('#multiline').input_value()=='first\n'
    assert page.evaluate('submits')==0
    page.locator('#multiline').press('Control+Enter');assert page.evaluate('submits')==1
    page.locator('#entry').press('Enter');assert page.evaluate('submits')==2
