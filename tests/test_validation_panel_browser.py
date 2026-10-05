"""The Validation panel renders closed, opens on click, and shows no raw ids."""
import os,socket,subprocess,sys,time
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright,expect

PANEL={"item_count":1,"groups":[
  {"need":"Parents can trust the reminders","uncovered":False,"items":[{"what_you_do":"Set a reminder and wait","what_to_look_for":"The phone buzzes","pass_when":"It buzzes on time","how_to_record_it":"Screen recording","where":"Physical iPhone","components":["api"],"automatic_checks":2}]},
  {"need":"Kids enjoy the app","uncovered":True,"items":[]}]}

@pytest.fixture(scope='module')
def base(tmp_path_factory):
 root=tmp_path_factory.mktemp('validation-ui')
 with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 env={**os.environ,'DELIVERY_DB_PATH':str(root/'ui.db'),'VMODEL_ARTIFACTS_DIR':str(root/'artifacts'),'PYTHONPATH':str(Path(__file__).parents[1]/'src')}
 p=subprocess.Popen([sys.executable,'-m','uvicorn','delivery_api.app:app','--host','127.0.0.1','--port',str(port)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 for _ in range(100):
  try:socket.create_connection(('127.0.0.1',port),.1).close();break
  except OSError:time.sleep(.1)
 yield f'http://127.0.0.1:{port}'
 p.terminate();p.wait(5)

def test_panel_is_closed_then_opens_without_ids(base):
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True);page=browser.new_page()
  page.goto(base+'/assets/validation-panel.js')
  page.set_content('<div id="host"></div>')
  page.evaluate("""async panel=>{const m=await import('%s/assets/validation-panel.js');document.querySelector('#host').innerHTML=m.validationPanelHtml(panel)}"""%base,PANEL)
  needs=page.locator('details.validation-need')
  expect(needs).to_have_count(2)
  assert page.locator('details[open]').count()==0
  expect(page.locator('.validation-flag')).to_have_text('Not Covered')
  needs.first.locator(':scope > summary').click()
  expect(needs.first.locator('details.validation-item')).to_have_count(1)
  needs.first.locator('details.validation-item > summary').click()
  expect(page.locator('.validation-row').first).to_be_visible()
  text=page.inner_text('#host')
  for raw in ('N-1','V-1','AC-','T-1'):assert raw not in text
  browser.close()
