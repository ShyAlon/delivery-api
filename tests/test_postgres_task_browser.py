"""Phase2A browser task contracts on authenticated PG, no SQLite removals."""


def test_create_rename_reload_delete_task(pg_browser):
    from playwright.sync_api import expect
    page, base, workspace = pg_browser
    page.goto(base)
    page.get_by_role('button', name='+ New task').click()
    title = page.locator('#newform input[name="title"]')
    page.wait_for_function("document.activeElement?.name==='title'")
    assert title.evaluate('e=>document.activeElement===e')
    title.fill('PG browser lifecycle')
    title.press('Enter')
    page.get_by_text("This task hasn't started.").wait_for()
    expect(page.get_by_role('heading', name='PG browser lifecycle')).to_be_visible()
    task_url = page.url
    page.reload()
    expect(page.get_by_role('heading', name='PG browser lifecycle')).to_be_visible()
    page.goto(base)
    card = page.locator('.task', has_text='PG browser lifecycle')
    card.get_by_role('button', name='Rename').click()
    page.locator('#renameform input[name=title]').fill('PG renamed lifecycle')
    page.get_by_role('button', name='Save name').click()
    page.get_by_text('PG renamed lifecycle', exact=True).wait_for()
    page.reload()
    card = page.locator('.task', has_text='PG renamed lifecycle')
    expect(card).to_have_count(1)
    from pathlib import Path
    import tempfile
    evidence = Path(tempfile.mkdtemp(prefix='pg-browser-parity-'))/'renamed.png'
    page.screenshot(path=str(evidence), full_page=True)
    print('PG_BROWSER_RENAMED_SCREENSHOT='+str(evidence))
    card.get_by_role('button', name='Delete').click()
    assert page.get_by_text('Original files in the artifacts folder stay on disk.').is_visible()
    page.get_by_role('button', name='Delete task').click()
    page.locator('#app').get_by_text('PG renamed lifecycle', exact=True).wait_for(state='detached')
    page.reload()
    expect(page.locator('.task')).to_have_count(0)
    response = page.request.get(base+'/api/tasks/'+task_url.split('/tasks/')[1].split('/')[0])
    assert response.status == 404


def test_create_textarea_keyboard_cancel_and_collision(pg_browser):
    from playwright.sync_api import expect
    page, base, workspace = pg_browser
    page.goto(base)
    page.get_by_role('button', name='+ New task').click()
    title = page.locator('#newform input[name="title"]')
    brief = page.locator('#newform textarea[name="brief"]')
    title.fill('PG multiline task')
    brief.fill('Line one')
    brief.press('Enter')
    brief.fill('Line one\nLine two')
    assert brief.input_value() == 'Line one\nLine two'
    assert page.locator('#newform').is_visible()
    brief.press('Control+Enter')
    page.get_by_text("This task hasn't started.").wait_for()
    assert page.get_by_role('heading', name='PG multiline task').is_visible()
    page.goto(base)
    page.locator('.task').first.wait_for()
    before = page.locator('.task').count()
    page.get_by_role('button', name='+ New task').click()
    page.locator('#newform input[name="title"]').fill('Must not be created')
    page.keyboard.press('Escape')
    assert page.locator('#newform').count() == 0
    assert page.locator('.task').count() == before
    page.get_by_role('button', name='+ New task').click()
    page.locator('#newform input[name="title"]').fill('PG MULTILINE TASK')
    with page.expect_response(lambda r:r.url.endswith('/api/tasks') and r.request.method=='POST') as submitted:
        page.locator('#newform input[name="title"]').press('Enter')
    response = submitted.value
    assert response.status == 409
    assert set(response.json()['detail']) == {'error','cause','next'}
    # Production showError replaces the create form with its actionable error dialog.
    expect(page.get_by_text('Choose a different task name or rename the existing task.', exact=False)).to_be_visible()
