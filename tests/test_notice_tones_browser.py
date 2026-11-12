"""Informational copy is neutral, without hiding genuine cautions or errors."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright,expect

WEB=Path(__file__).parents[1]/'src/delivery_api/web'

def test_next_phase_information_is_neutral_and_failed_submit_still_warns(tmp_path):
 with sync_playwright() as p:
  browser=p.chromium.launch(args=['--no-sandbox'])
  for width,height in ((1475,780),(390,844)):
   page=browser.new_page(viewport={'width':width,'height':height})
   def route(r):
    path=r.request.url.split('notice.test',1)[1]
    if path.startswith('/api/'):
     if (r.request.post_data_json or {}).get('confirm'):r.fulfill(status=409,json={'error':'Start blocked','cause':'Current state changed','next':'Refresh before trying again'})
     else:r.fulfill(json={'label':'Stakeholder needs','message':'This phase uses your credits as it runs. You can stop it at any time.','can_start':True})
    elif path.endswith(('.js','.css')):r.fulfill(content_type='text/javascript' if path.endswith('.js') else 'text/css',body=(WEB/path.rsplit('/',1)[1]).read_text())
    else:r.fulfill(content_type='text/html',body='<link rel="stylesheet" href="/style.css"><main id="app"><button id="startnext">Start next phase</button></main><div id="modal" class="modal hidden"></div>')
   page.route('http://notice.test/**',route);page.goto('http://notice.test/')
   page.evaluate("""async()=>{const {Page}=await import('/page-component.js');Page.installStyles();const {Dialog}=await import('/shared-dialog.js');const {createTaskActions}=await import('/taskactions.js');const modal=document.querySelector('#modal');const a=createTaskActions({openDialog:(html,options)=>Dialog.open(modal,html,options),closeModal:()=>Dialog.close(modal),detail:async()=>{},showError:e=>{throw e}});await a.showNextPhase({task_id:'fixture'})}""")
   note=page.get_by_text('Your session cost and credits left stay in the top bar while it runs.')
   expect(note).to_have_class('notice info')
   assert note.evaluate('e=>getComputedStyle(e).borderLeftWidth')=='1px'
   assert note.evaluate('e=>getComputedStyle(e).backgroundColor')!='rgb(255, 242, 217)'
   page.screenshot(path=str(tmp_path/f'neutral-notice-{width}.png'))
   page.locator('#confirmnext').click();expect(page.locator('#nextstatus .notice')).to_contain_text('Start blocked')
   assert page.locator('#nextstatus .notice').evaluate('e=>getComputedStyle(e).backgroundColor')=='rgb(255, 242, 217)'
   page.close()
  browser.close()
