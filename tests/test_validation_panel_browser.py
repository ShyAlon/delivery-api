"""The Validation panel renders closed, opens on click, and shows no raw ids."""
import pytest
from playwright.sync_api import expect

PANEL={"item_count":1,"groups":[
  {"need":"Parents can trust the reminders","uncovered":False,"items":[{"what_you_do":"Set a reminder and wait","what_to_look_for":"The phone buzzes","pass_when":"It buzzes on time","how_to_record_it":"Screen recording","where":"Physical iPhone","components":["api"],"automatic_checks":2}]},
  {"need":"Kids enjoy the app","uncovered":True,"items":[]}]}

def test_panel_is_closed_then_opens_without_ids(pg_browser):
 page,base,workspace=pg_browser
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
