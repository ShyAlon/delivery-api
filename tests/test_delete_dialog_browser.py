"""Deleting a task from its own page must close the dialog when leaving the page.

The delete dialog lives in the page-level #modal container, a sibling of #app.
Navigating from the task page back to the dashboard swaps only #app, so before
the fix the "Deleting…" dialog stayed on screen forever after a successful
delete. The confirm path now closes the modal before navigating.
"""
import pytest
from delivery_api.tenancy import tenant_context


@pytest.fixture
def ui(pg_browser):
    page,base,workspace=pg_browser
    with tenant_context(workspace.scope):
        workspace.store.create_task('Doomed definition','To be deleted',task_id='doomed')
        workspace.store.update_phase('doomed','intent','done',detail='Approved')
    return page,base


def test_delete_from_task_page_closes_the_dialog(ui):
    page,ui=ui
    # No mocks: the restricted PG store deletes the task, so the dashboard must
    # both lose the stuck dialog AND no longer list the deleted task.
    page.goto(ui + "/tasks/doomed/view")
    page.locator("#deletetask").wait_for(timeout=10000)
    page.locator("#deletetask").click()
    dialog = page.locator("#modal .dialog")
    dialog.wait_for(timeout=5000)
    assert "Doomed definition" in dialog.inner_text()
    page.locator("#confirmdelete").click()
    page.wait_for_url(ui + "/", timeout=10000)
    # The modal must be closed and empty: no stuck "Deleting…" dialog on the dashboard.
    page.wait_for_function("() => { const m = document.querySelector('#modal'); return m.classList.contains('hidden') && m.innerHTML === '' }", timeout=5000)
    assert page.locator("#modal .dialog").count() == 0
    # The deletion is real: the dashboard no longer lists the task.
    assert "Doomed definition" not in page.locator("#app").inner_text()
