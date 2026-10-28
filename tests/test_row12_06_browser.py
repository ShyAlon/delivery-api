from test_debug_progress_browser import ui, page, _open_debug


def test_cloud_execution_sections_are_policy_excluded_without_reads(page,ui,tmp_path):
    requests=[]
    page.on('request',lambda r:requests.append(r.url))
    _open_debug(page,ui)
    page.locator('#debugexecution details').nth(3).wait_for()
    for index in (1,3):
        section=page.locator('#debugexecution details').nth(index)
        section.evaluate('el=>el.open=true')
        assert 'policy_excluded' in section.inner_text()
        assert 'Not collected by this mode policy' in section.inner_text()
        assert 'read_failed' not in section.inner_text()
    assert not any('/execution?debug=1' in url or '/classifications' in url for url in requests)
    page.locator('#debugexecution').scroll_into_view_if_needed()
    page.screenshot(path=str(tmp_path/'cloud-excluded-sections.png'),full_page=True)


def test_permitted_diagnostics_failure_is_unavailable_not_empty_or_raw(page,ui,tmp_path):
    page.route('**/api/tasks/dbg/diagnostics',lambda r:r.fulfill(status=503,content_type='application/json',body='{"detail":"private-error"}'))
    _open_debug(page,ui)
    section=page.locator('#debugexecution details').nth(0)
    section.wait_for()
    assert 'unavailable' in section.inner_text()
    assert 'read_failed' in section.inner_text()
    assert 'private-error' not in page.locator('#debugexecution').inner_text()
    assert 'policy_excluded' not in section.inner_text()
    section.scroll_into_view_if_needed()
    page.screenshot(path=str(tmp_path/'permitted-read-failure.png'),full_page=True)
