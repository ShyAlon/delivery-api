"""Loading placeholders must not masquerade as durable task cards."""
from test_ui_playwright import pg_ids,pg_ui,pg_page
from playwright.sync_api import expect


def test_delayed_dashboard_keeps_placeholder_and_task_identity_separate(pg_page,pg_ui):
    page=pg_page
    held=[]
    page.route('**/api/tasks',lambda route:held.append(route))
    page.goto(pg_ui)
    page.wait_for_function("document.querySelectorAll('.task-skeleton').length===3")
    assert len(held)==1
    assert page.locator('.task-skeleton').count()==3
    page.screenshot(path='/tmp/dashboard-loading-after.png',full_page=True)
    try:
        assert page.locator('.task').count()==0,'loading skeletons are not durable task cards'
    finally:
        held[0].fulfill(response=held[0].fetch())
    expect(page.locator('.task')).to_have_count(5)
    assert page.locator('.task-skeleton').count()==0
    assert page.locator('.v-map svg').count()==5
    page.screenshot(path='/tmp/dashboard-loaded-after.png',full_page=True)
