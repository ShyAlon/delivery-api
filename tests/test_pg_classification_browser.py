"""Classification Debug section reads owned PG evidence, not local residue."""
from test_ui_playwright import pg_ui,pg_page,pg_ids
from playwright.sync_api import expect


def test_debug_reads_pg_classification_evidence(pg_page,pg_ui,pg_ids,pg_browser):
    import json
    from delivery_api.tenancy import tenant_context
    from test_http_cutover import _PGUsage
    page=pg_page;workspace=pg_browser[2];tid=pg_ids['live']
    q,a='Which view should open first?','A calm shared view'
    with tenant_context(workspace.scope):
        workspace.store.set_engine_session(tid,'classification-browser')
        workspace.store.add_event(tid,'waiting_input',q,phase_key='intent',payload={'question':q,'waiting_input':True})
        workspace.store.add_event(tid,'answer','You: '+a,phase_key='intent',payload={'answer':a})
        record={'slot':'coverage','timestamp':'fixture','input':{'state':q+'\n'+a},
            'baseline':{'status':'ok','verdict':{'relevance':'relevant'}},
            'jev':{'status':'ok','verdict':{'relevance':'thin'}}}
        _PGUsage(workspace.store,tid,'interview/categorization-shadow.jsonl').write_text(json.dumps(record)+'\n')
    page.goto(pg_ui+'/tasks/'+tid+'/view')
    page.locator('#accountbutton').click();page.get_by_role('menuitem',name='Debug',exact=True).click()
    section=page.locator('#debugexecution details').filter(has=page.locator('summary',has_text='Answer classifications'))
    section.locator('summary').click()
    expect(section).to_contain_text('"differed": true')
    expect(section).to_contain_text('"relevance": "thin"')
    section.scroll_into_view_if_needed()
    page.screenshot(path='/downloads/pg-classifications-desktop.png',full_page=False)
    page.set_viewport_size({'width':390,'height':844})
    section.scroll_into_view_if_needed()
    page.screenshot(path='/downloads/pg-classifications-mobile.png',full_page=False)
