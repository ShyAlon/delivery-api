"""Remember the editable email only, never password or authentication tokens."""
def test_signin_email_prefills_overwrites_clears_and_enter_submits(pg_browser,tmp_path):
    page,base,workspace=pg_browser
    page.context.clear_cookies()
    # Frontend storage/submission contract, not an external Supabase request.
    page.route('**/auth/signin',lambda r:r.fulfill(status=401,json={'detail':'Fixture sign-in refused'}))
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
    page.screenshot(path=str(tmp_path/'remember-signin-email.png'),full_page=True)
    email.fill('');page.reload();email.wait_for();assert email.input_value()==''
    assert page.evaluate("localStorage.getItem('requi-re.signin-email')") is None
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=str(tmp_path/'remember-signin-email-mobile.png'),full_page=True)
    # Private/blocked storage must never prevent authentication submission.
    page.add_init_script("Storage.prototype.getItem=Storage.prototype.setItem=Storage.prototype.removeItem=()=>{throw new Error('blocked storage')}")
    page.reload();email.wait_for();email.fill('blocked@example.test')
    password.fill('Remember fake 42!');password.press('Enter')
    page.locator('#authmessage .notice').wait_for()
