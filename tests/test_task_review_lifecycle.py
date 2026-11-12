"""Real hermetic interview: leaving task progress must retire its live callbacks."""
import test_crosshost_interview_acceptance as X
from test_crosshost_interview_acceptance import dsn,keys,AdmittedHost,sign_in,create_task,pump_interview
pytestmark=X.pytestmark

def test_concept_submit_keeps_the_new_approval_review_mounted(dsn,keys,tmp_path_factory):
    from playwright.sync_api import sync_playwright,expect
    root=tmp_path_factory.mktemp('diagnostic-mount')
    host=AdmittedHost('local',root,keys,dsn)
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            page=browser.new_page()
            sign_in(page,host.base)
            tid=create_task(page,host.base,'Minimal review ownership','A SaaS web app for surf schools with booking and logins')
            page.locator('#startengine').click()
            answers=['Customers pick a slot and pay online.','They pay by card when they book.','Rainy days are automatically refunded.']
            pump_interview(page,host.base,tid,answers,finish=False,timeout=30)
            # The current UI may already auto-open the concept decision.
            # Requiring a launcher after navigation races the real route.
            page.wait_for_url('**/decisions/**')
            page.locator('input[name="choice"]').first.check()
            page.locator('textarea[name="rationale"]').fill('Deterministic first concept')
            old=page.url
            page.get_by_role('button',name='Choose and continue',exact=True).click()
            page.wait_for_function('old=>location.href!==old',arg=old)
            page.wait_for_timeout(1000)
            page.screenshot(path=str(root/'approval-review.png'),full_page=True)
            assert page.locator('#reviewform').count()==1,'current approval review was replaced after concept submit'
            assert page.locator('.detail-head').count()==0,'task view owns the approval-review URL'
            browser.close()
    finally:host.stop()
