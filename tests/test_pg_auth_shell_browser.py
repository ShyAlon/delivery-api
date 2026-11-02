"""Cloud shell outcomes survive local-auth implementation test retirement."""
from test_artifacts import evidence_path
from playwright.sync_api import expect


def test_pg_shell_home_boot_and_signed_reload_have_no_browser_errors(pg_browser):
    page,base,workspace=pg_browser
    errors=[]
    page.on('console',lambda m:errors.append('console: '+m.text) if m.type=='error' else None)
    page.on('pageerror',lambda e:errors.append('pageerror: '+str(e)))
    page.context.clear_cookies()
    page.goto(base+'/')
    expect(page.get_by_text('Welcome to Requi.re',exact=True)).to_be_visible()
    assert 'Loading workspace' not in page.locator('body').inner_text()
    page.evaluate('window.__guardToken=Math.random()')
    page.get_by_role('link',name='Home',exact=True).click()
    assert page.evaluate('window.__guardToken') is not None and page.url==base+'/'
    assert errors==[],errors
    page.context.add_cookies([{'name':'requi_access','value':workspace.token,'url':base}])
    page.reload()
    expect(page.get_by_role('button',name='+ New task')).to_be_visible()
    expect(page.locator('.avatar')).to_be_visible()
    page.reload()
    expect(page.locator('.avatar')).to_be_visible()
    assert 'Loading workspace' not in page.locator('body').inner_text()
    assert errors==[],errors
    page.screenshot(path=evidence_path('pg-shell-signed-reload.png'))


def test_pg_shell_logout_returns_to_signin_and_clears_cookie(pg_browser,monkeypatch):
    from delivery_api import supabase_sessions
    calls=[]
    def logout(path,payload,*,access=None):
        calls.append((path,payload,access))
        return {}
    monkeypatch.setattr(supabase_sessions,'_call',logout)
    page,base,workspace=pg_browser
    original=next(c['value'] for c in page.context.cookies() if c['name']=='requi_access')
    page.goto(base+'/')
    page.locator('#accountbutton').click()
    page.get_by_role('menuitem',name='Sign out',exact=True).click()
    expect(page.get_by_text('Welcome to Requi.re',exact=True)).to_be_visible()
    expect(page.get_by_role('button',name='Sign in',exact=True).first).to_be_visible()
    assert len(calls)==1 and calls[0][0]=='logout?scope=global' and calls[0][1]=={} and calls[0][2]==original
    assert not any(c['name'] in ('requi_access','requi_refresh') for c in page.context.cookies())
    page.reload()
    expect(page.locator('#authform')).to_be_visible()
    page.screenshot(path=evidence_path('pg-shell-signed-out.png'))
