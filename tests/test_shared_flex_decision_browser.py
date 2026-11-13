"""Shared flex contract across real current and historical gate layouts."""
import pytest
from playwright.sync_api import expect

@pytest.mark.parametrize('width,height',[(1475,780),(390,844),(640,480)])
@pytest.mark.parametrize('historical',[False,True])
def test_shared_flex_gate_top_actions_and_aligned_panels(pg_browser,width,height,historical):
 from test_decision_pages_browser import _stack_review
 from urllib.parse import urlparse
 p,base,w=pg_browser;data=_stack_review()
 data['actionable']=not historical;data['historical']=historical
 data['review']['documents'][0]['view']['content']='{"paragraph":"'+('Long requirements evidence. '*150)+'"}'
 def route(r):
  path=urlparse(r.request.url).path
  if path.endswith('/review'):r.fulfill(json=data);return
  if path=='/api/tasks/t1':r.fulfill(json=data['review']['task']);return
  if path=='/api/model-routes':r.fulfill(json={'routes':[]});return
  if path.endswith('/stream'):r.fulfill(content_type='text/event-stream',body='');return
  if path.endswith('/cost'):r.fulfill(json={});return
  r.continue_()
 p.route('**/api/**',route);p.set_viewport_size({'width':width,'height':height})
 p.goto(base+'/tasks/t1/gates/gate-architecture')
 evidence=p.locator('.gate-evidence');controls=p.locator('#gateform');actions=controls.locator('.shared-action-strip')
 expect(actions).to_be_visible()
 if historical:
  expect(p.locator('.shared-decision-summary')).to_have_count(0)
  expect(controls).not_to_contain_text('Your decision is needed')
  expect(controls.locator('[data-action=approve]')).to_be_disabled()
 else:
  for action in ('approve','edit','redo'):
   button=controls.locator('[data-action='+action+']')
   expect(button).to_be_visible();expect(button).to_be_in_viewport()
  p.evaluate('window.scrollTo(0,400)')
  expect(controls.locator('[data-action=approve]')).to_be_in_viewport()
 assert p.evaluate('document.documentElement.scrollWidth<=innerWidth')
 if width>850:
  p.evaluate('scrollTo(0,0)')
  assert abs(evidence.bounding_box()['y']-controls.bounding_box()['y'])<2
  assert controls.bounding_box()['width']>=350
  if historical:assert actions.bounding_box()['width']<=controls.bounding_box()['width']
  else:assert actions.bounding_box()['width']>=width-2
 p.screenshot(path=f'/tmp/flex-gate-{width}-{historical}.png',full_page=False)
