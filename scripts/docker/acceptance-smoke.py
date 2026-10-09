"""Installed-wheel presenter/local browser proof. Fictional review, no acceptance authority."""
import hashlib,json,os
from pathlib import Path
from playwright.sync_api import sync_playwright
from delivery_api.revision_acceptance_view import revision_acceptance_view
import delivery_api
assert 'site-packages' in delivery_api.__file__ and not os.environ.get('PYTHONPATH')
for name in ('OPENAI_API_KEY','DEEPSEEK_API_KEY','SUPABASE_SERVICE_ROLE_KEY','DELIVERY_POSTGRES_DSN'):
    assert not os.environ.get(name),'no live credentials'
root=Path(__file__).resolve().parent
artifact=json.loads((root/'private-ui/revision-source.json').read_text())['srs']
digest=lambda value:hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
review={'kind':'closed_revision_installation_review','state':'awaiting_review','accepted':False,'installed':False,
    'dispatch_started':False,'installation_available':False,'phase':'srs','semantic_session_id':artifact['session_id'],
    'regenerated_artifact_sha256':digest(artifact),'phases_requiring_reapproval':['srs','architecture'],
    'cost_review':{'warning':'Continuing Affected Work Incurs Extra Cost.'}}
view=revision_acceptance_view(review=review,review_sha256=digest(review),artifact=artifact,spent_credits=4.2)
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1100,'height':1050});unexpected=[]
    def isolate(route):
        if route.request.url.startswith((root/'private-ui').as_uri()+'/'):route.continue_()
        else:unexpected.append(route.request.url);route.abort()
    page.route('**/*',isolate);page.goto((root/'private-ui/revision-acceptance.html').as_uri())
    page.evaluate('''review=>window.createRevisionAcceptance({root:document.querySelector('#page'),review,adapter:{async saveDraft(){},async submit(input){window.sent=input;return {review_sha256:'f'.repeat(64),complete:true}}}})''',view)
    assert artifact['functional'][0]['statement'] in page.locator('#page').inner_text()
    page.locator('[value=edit]').check();page.locator('#change').fill('Keep Fictional Guidance');page.locator('#ack').check();page.locator('#continue').click()
    page.wait_for_function("document.querySelector('#status').textContent.includes('Review Changed')")
    assert page.evaluate('window.sent.review_sha256')==digest(review)
    assert page.locator('#change').input_value()=='Keep Fictional Guidance'
    page.screenshot(path='/tmp/acceptance-installed-desktop.png',full_page=True)
    page.set_viewport_size({'width':390,'height':844});page.screenshot(path='/tmp/acceptance-installed-mobile.png',full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth') and not unexpected
    browser.close()
print(json.dumps({'private_acceptance_installed_wheel':'passed','fixture':'fictional exact digest, no authority','stale_review_rejected':True,'live_effects':False}))
