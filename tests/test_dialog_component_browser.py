"""The shared Dialog owns geometry, focus, dismissal and native construction."""
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
WEB=Path(__file__).parents[1]/'src/delivery_api/web'

def test_overlay_and_native_dialog_share_styles_and_keyboard_lifecycle(tmp_path):
    with sync_playwright() as p:
        browser=p.chromium.launch(args=['--no-sandbox']);page=browser.new_page(viewport={'width':390,'height':500})
        page.route('http://dialog.test/**',lambda route:route.fulfill(content_type='text/javascript' if route.request.url.endswith('.js') else 'text/css',body=(WEB/route.request.url.split('/')[-1]).read_text()) if route.request.url.endswith(('.js','.css')) else route.fulfill(content_type='text/html',body='<link rel="stylesheet" href="/style.css"><button id="origin">Open</button><div id="modal" class="modal hidden"></div>'))
        page.goto('http://dialog.test/');page.locator('#origin').focus()
        page.evaluate("""async()=>{const {Dialog}=await import('/shared-dialog.js');window.Dialog=Dialog;
          Dialog.open(document.querySelector('#modal'),Dialog.html('<h2>Review changes</h2><input id="field"><button id="last">Done</button>'),{focus:'#field'});
        }""")
        expect(page.locator('#field')).to_be_focused()
        page.locator('#last').focus();page.keyboard.press('Tab');expect(page.locator('#field')).to_be_focused()
        page.keyboard.press('Escape');expect(page.locator('#modal')).to_be_empty();expect(page.locator('#origin')).to_be_focused()
        page.evaluate("""()=>{const d=Dialog.create('Long review');for(let i=0;i<30;i++){const p=document.createElement('p');p.textContent='Evidence line '+i;d.append(p)}const button=document.createElement('button');button.textContent='Close review';button.onclick=()=>d.close();d.append(button);d.showModal()}""")
        dialog=page.locator('dialog');expect(dialog).to_be_visible()
        assert dialog.evaluate('e=>e.scrollHeight>e.clientHeight')
        assert dialog.evaluate('e=>{const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight}')
        page.get_by_role('button',name='Close review').scroll_into_view_if_needed()
        expect(page.get_by_role('button',name='Close review')).to_be_in_viewport()
        page.screenshot(path='/downloads/shared-dialog-short-mobile.png')
        page.get_by_role('button',name='Close review').click();expect(dialog).to_have_count(0)
        assert page.locator('#ui-dialog-styles').count()==1
        browser.close()
