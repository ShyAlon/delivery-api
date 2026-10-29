"""Current idle gates are read-only, not historical revisions."""
import pytest
from delivery_api.tenancy import tenant_context


@pytest.mark.parametrize('mode',['idle','waiting','historical'])
def test_gate_notice_distinguishes_revision_age_from_actionability(pg_browser,mode,tmp_path):
    from playwright.sync_api import expect
    page,base,workspace=pg_browser
    with tenant_context(workspace.scope):
        s=workspace.store;tid=s.create_task('Gate label')['task_id']
        if mode!='idle':s.wait_at_gate(tid,'needs')
    endpoint=f'{base}/api/tasks/{tid}/gates/gate-needs/review'
    original=page.request.get(endpoint).json()
    revision=original['review']['revision']
    if mode=='historical':
        with tenant_context(workspace.scope):s.decide_gate(tid,'gate-needs','approve',selected_option='Proceed')
    suffix='?revision='+revision if mode=='historical' else ''
    data=page.request.get(endpoint+suffix).json()
    page.goto(f'{base}/tasks/{tid}/gates/gate-needs'+suffix)
    expect(page.locator('.gate-page')).to_be_visible()
    if mode=='historical':
        expect(page.get_by_text('Historical gate review - read-only.',exact=False)).to_be_visible()
        expect(page.get_by_text('This is a past review and is read-only.',exact=False)).to_be_visible()
    elif mode=='idle':
        expect(page.get_by_text('Current gate review - not waiting for a decision.',exact=False)).to_be_visible()
        expect(page.get_by_text('This gate is not waiting for a decision.',exact=False)).to_be_visible()
        expect(page.get_by_text('Historical gate review',exact=False)).to_have_count(0)
    else:
        expect(page.get_by_text('Historical gate review',exact=False)).to_have_count(0)
        expect(page.get_by_text('Current gate review - not waiting',exact=False)).to_have_count(0)
    assert data['historical'] is (mode=='historical')
    assert data['actionable'] is (mode=='waiting')
    assert data['review']['documents']==[]  # labeling does not invent missing evidence
    page.screenshot(path=str(tmp_path/(mode+'-gate-label.png')),full_page=True)
    print('GATE_LABEL_SCREENSHOT='+str(tmp_path/(mode+'-gate-label.png')))
