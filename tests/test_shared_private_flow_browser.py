from test_backoffice_shared_ui import ui
from playwright.sync_api import sync_playwright,expect

def test_private_tabs_footer_and_dialog_inherit_one_scroll(ui):
 base,calls=ui
 with sync_playwright() as pw:
  browser=pw.chromium.launch(args=['--no-sandbox'])
  p=browser.new_page(extra_http_headers={'Authorization':'Bearer fake'})
  for width,height in ((1475,780),(1280,720),(390,844)):
   p.set_viewport_size({'width':width,'height':height});p.goto(base+'/#jobs')
   expect(p.locator('#active-jobs tbody tr').first).to_be_visible()
   for selector in ('header','.tabs','.operator-bottom-bar'):
    assert p.locator(selector).first.evaluate('e=>getComputedStyle(e).position') not in ('sticky','fixed')
   p.evaluate('window.scrollTo(0,0)');p.screenshot(path=f'/tmp/shared-private-{width}.png')
  p.evaluate('''async()=>{const {Dialog}=await import('/assets/shared-dialog.js');const d=Dialog.create('Long plan');const plan=document.createElement('pre');plan.className='force-plan';plan.textContent=Array.from({length:120},(_,i)=>'Step '+i+' review this action').join('\\n');d.append(plan);Dialog.actions(d,{label:'Fixture only',run:async()=>{}});d.showModal()}''')
  dialog=p.locator('dialog');expect(dialog).to_be_visible()
  assert dialog.evaluate('e=>e.scrollHeight>e.clientHeight')
  assert p.locator('.force-plan').evaluate('e=>e.scrollHeight<=e.clientHeight+1')
  assert p.locator('body').evaluate('e=>getComputedStyle(e).overflowY')=='hidden'
  p.get_by_role('button',name='Cancel',exact=True).scroll_into_view_if_needed()
  p.screenshot(path='/tmp/shared-private-dialog-390.png')
  p.keyboard.press('Escape');expect(dialog).to_have_count(0)
  assert not [r for r in calls if r.method=='POST']
  browser.close()
