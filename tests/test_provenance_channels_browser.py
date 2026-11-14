"""Real gate rendering must distinguish a quote, a preference and model reasoning."""
from urllib.parse import urlparse
import pytest
from playwright.sync_api import expect
from delivery_api.decision_provenance import project_decisions,SUPPORT_RULE
from vmodel_contracts.semantic_review import digest
from test_decision_provenance_channels import _adr

@pytest.mark.parametrize('width,height',[(1475,950),(390,844),(640,480)])
def test_gate_provenance_channels_source_support_and_readable_fonts(pg_browser,width,height):
 from test_decision_pages_browser import _stack_review
 page,base,_=pg_browser;data=_stack_review();adr=_adr()
 finding={'finding_id':'F-fixture','rule_id':SUPPORT_RULE,'targets':['ADR-1'],'status':'OPEN',
          'summary':'Compare the recorded constraint','artifact_revision':digest({'adr':adr,'sources':adr['founder_citations']})}
 view=data['review']['documents'][0]['view']
 view['decision_provenance']=project_decisions({'decisions':[adr]},[],{'availability':'available'},{'findings':{'F-fixture':finding}})
 def route(r):
  path=urlparse(r.request.url).path
  if path.endswith('/review'):r.fulfill(json=data);return
  if path=='/api/tasks/t1':r.fulfill(json=data['review']['task']);return
  if path=='/api/model-routes':r.fulfill(json={'routes':[]});return
  if path.endswith('/stream'):r.fulfill(content_type='text/event-stream',body='');return
  if path.endswith('/cost'):r.fulfill(json={});return
  r.continue_()
 page.route('**/api/**',route);page.set_viewport_size({'width':width,'height':height})
 page.goto(base+'/tasks/t1/gates/gate-architecture')
 sources=page.locator('.decision-provenance');expect(sources).to_be_visible()
 expect(sources).to_contain_text('Recorded founder constraint; architecture choice needs review')
 expect(sources).to_contain_text('Do nothing on an unreadable page')
 expect(sources).to_contain_text('Recorded finding state: OPEN')
 expect(sources).to_contain_text('A quotation is not founder approval')
 sources.get_by_text('Explicit technology preference',exact=True).click()
 expect(sources).to_contain_text('This does not mean the founder supplied no constraints.')
 sources.get_by_text('Model rationale',exact=True).click();expect(sources).to_contain_text('It can read the active DOM')
 sources.get_by_text('Retained source snapshot',exact=True).click();expect(sources).to_contain_text(adr['founder_citations'][0]['source_revision'])
 overflow=page.locator('main *').evaluate_all("es=>es.filter(e=>e.getBoundingClientRect().right>innerWidth+1).slice(0,15).map(e=>({tag:e.tagName,cls:e.className,right:e.getBoundingClientRect().right,width:e.getBoundingClientRect().width}))")
 assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),overflow
 sizes=page.locator('header,main,#modal').evaluate_all('''roots=>roots.flatMap(root=>[...root.querySelectorAll('*')]).filter(e=>e.getClientRects().length&&[...e.childNodes].some(n=>n.nodeType===3&&n.textContent.trim())).map(e=>({tag:e.tagName,cls:e.className,size:parseFloat(getComputedStyle(e).fontSize)})).filter(e=>e.size<18)''')
 assert sizes==[],sizes
 if width<700:assert sources.locator('blockquote').bounding_box()['width']>=230
 page.evaluate('scrollTo(0,0)');page.screenshot(path=f'/tmp/provenance-channels-{width}.png',full_page=False)
