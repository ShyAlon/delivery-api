"""A refresh owns only its transient error, never edits, blockers or action results."""
from copy import deepcopy
from urllib.parse import urlparse
from playwright.sync_api import expect
import pytest

@pytest.mark.parametrize('failure_at',['task','review'])
def test_gate_refresh_failure_clears_only_after_full_success(pg_browser,failure_at):
 from test_decision_pages_browser import _stack_review
 p,base,w=pg_browser;data=_stack_review();state={'failure':None,'reviews':0}
 def route(r):
  path=urlparse(r.request.url).path
  if path.endswith('/review'):
   state['reviews']+=1
   if state['failure']=='review':r.fulfill(status=500,json={'detail':'Fixture review unavailable'});return
   r.fulfill(json=deepcopy(data));return
  if path=='/api/tasks/t1':
   if state['failure']=='task':r.fulfill(status=500,json={'detail':'Fixture checkout timeout'});return
   r.fulfill(json=data['review']['task']);return
  if path=='/api/model-routes':r.fulfill(json={'routes':[]});return
  if path.endswith('/stream'):r.fulfill(content_type='text/event-stream',body='');return
  if path.endswith('/cost'):r.fulfill(json={});return
  r.continue_()
 p.route('**/api/**',route);p.goto(base+'/tasks/t1/gates/gate-architecture')
 error=p.locator('#gate-refresh-status');expect(error).to_have_text('')
 p.locator('[name=comment]').fill('Keep my draft')
 p.locator('[name=edited_text]').fill('Keep my decision text')
 p.evaluate("document.querySelector('#gate-decision-status').textContent='Draft saved. Keep this action result.'")
 state['failure']=failure_at
 expect(error).to_contain_text('Could not refresh gate evidence',timeout=15000)
 # A task success followed by review failure must NOT clear the error.
 state['failure']='review';p.wait_for_timeout(5500)
 expect(error).to_contain_text('Could not refresh gate evidence')
 state['failure']=None
 expect(error).to_have_text('',timeout=15000)
 expect(p.locator('[name=comment]')).to_have_value('Keep my draft')
 expect(p.locator('[name=edited_text]')).to_have_value('Keep my decision text')
 expect(p.locator('#gate-decision-status')).to_have_text('Draft saved. Keep this action result.')
 # ADR decisions remain unreviewed in this fixture. Recovery does not bypass them.
 expect(p.locator('[data-action=approve]')).to_be_disabled()
 # A changed authoritative review still blocks approval after transport recovery.
 data['review']['revision']='changed-review'
 expect(p.locator('#gate-decision-status')).to_contain_text('Gate evidence changed',timeout=15000)
 state['failure']='task';expect(error).to_contain_text('Could not refresh gate evidence',timeout=15000)
 state['failure']=None;p.wait_for_timeout(5500)
 expect(p.locator('#gate-decision-status')).to_contain_text('Gate evidence changed')
 expect(p.locator('[data-action=approve]')).to_be_disabled()
