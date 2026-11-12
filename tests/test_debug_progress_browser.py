"""The Debug dialog must show the real build identity and honest collection progress.

The image already carries the four repository SHAs in REQUI_BUILD_REPOS; /health
now returns them in tenant mode too, and the dialog shows them instead of the
opaque runtime id. Downloading the everything ZIP reports progress per section
and finishes with an honest count, naming any section that could not load
instead of silently dropping it.
"""
import time
import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

REPOS = "delivery-api=abcdef12,interview-engine=12345678,spec-generator=90abcdef,verification-loop=abcdef01"
LABEL = "delivery-api abcdef12, interview-engine 12345678, spec-generator 90abcdef, verification-loop abcdef01"


@pytest.fixture
def ui(pg_browser,monkeypatch):
    page,base,workspace=pg_browser
    from delivery_api.tenancy import tenant_context
    from delivery_api import config
    import jwt
    from pg_workspace import KEY
    monkeypatch.setenv('REQUI_BUILD_ID','local-9')
    monkeypatch.setenv('REQUI_BUILD_REPOS',REPOS)
    with tenant_context(workspace.scope):
        workspace.store.create_task('Debug subject','Diagnostics target',task_id='dbg'+workspace.scope.owner_id[:8])
        workspace.store.update_phase('dbg'+workspace.scope.owner_id[:8],'intent','done',detail='Approved')
    claims=jwt.decode(workspace.token,options={'verify_signature':False})
    claims['email']=config.OPERATOR_EMAIL
    page.context.add_cookies([{'name':'requi_access','value':jwt.encode(claims,KEY,algorithm='ES256'),'url':base}])
    page._debug_task_id='dbg'+workspace.scope.owner_id[:8]
    page.context.grant_permissions(['clipboard-read','clipboard-write'])
    import delivery_api.app as module
    monkeypatch.setattr(module,'RUNTIME_VERSION',module._runtime_version())
    return base


@pytest.fixture
def page(ui,pg_browser):
    return pg_browser[0]


def _open_debug(page, ui):
    page.goto(ui + '/tasks/'+page._debug_task_id+'/view')
    page.locator("#accountbutton").wait_for(timeout=10000)
    page.locator("#accountbutton").click()
    page.locator("#opendebug").click()
    page.locator(".debug-dialog").wait_for(timeout=5000)


def test_debug_dialog_shows_repository_build_identity(page, ui):
    _open_debug(page, ui)
    badge = page.locator(".debug-dialog .runtime-id")
    badge.wait_for(timeout=5000)
    page.wait_for_function(
        "() => document.querySelector('.debug-dialog .runtime-id').textContent.includes('delivery-api abcdef12')",
        timeout=10000)
    assert badge.inner_text() == f"BUILD {LABEL}"
    page.locator(".debug-dialog .dialog-x").click()


def test_everything_zip_reports_per_section_progress_and_honest_counts(page, ui, tmp_path):
    def slow_offline_logs(route):
        time.sleep(0.8)  # hold the section so the per-section progress line is observable
        route.fulfill(status=500, content_type="application/json", body='{"detail":"log store offline"}')
    page.route("**/api/server-log*", slow_offline_logs)
    _open_debug(page, ui)
    with page.expect_download(timeout=15000) as dl:
        page.locator("#downloadeverything").click()
        # Per-section progress while collecting. The section INDEX is not pinned:
        # sections come and go (Request Timing joined 2026-10-26) - pin the name.
        page.wait_for_function(
            "() => {const t=document.querySelector('#debugstatus').textContent;return t.includes('Collecting section')&&t.includes('Server logs (last 30 min)')}",
            timeout=10000)
    status = page.locator("#debugstatus")
    page.wait_for_function(
        "() => document.querySelector('#debugstatus').textContent.startsWith('Downloaded requi-everything.zip')",
        timeout=10000)
    text = status.inner_text()
    download = dl.value
    target = tmp_path / "everything.zip"
    download.save_as(target)
    with zipfile.ZipFile(target) as z:
        body = z.read("requi-everything.txt").decode()
    import json,re
    manifest_text=body.split('===== Evidence manifest and DB projections =====\n',1)[1].split('\n\n===== Debug info =====',1)[0]
    manifest=json.JSONDecoder().raw_decode(manifest_text)[0]
    timings=json.loads(body.split('===== Export collector timings =====\n',1)[1])
    assert timings['total_ms']>=0 and all(x['duration_ms']>=0 for x in timings['stages'] if x['duration_ms'] is not None)
    assert manifest['version']==2 and manifest['task_id']==page._debug_task_id
    allowed=[entry for entry in manifest['sections'] if entry['availability']!='policy_excluded']
    assert all(entry['availability'] in ('available','unavailable') for entry in allowed)
    fixed=['Full task call and cost evidence','Task-window retained logs','Debug info','Task diagnostics (question, control state, engine state)','Run events','Server logs (last 30 min)','Client logs (this tab)']
    assert all('===== '+title+' =====' in body for title in fixed)
    missing=[entry['source'] for entry in allowed if entry['availability']=='unavailable']+['Task-window retained logs','Server logs (last 30 min)']
    total=len(allowed)+len(fixed)
    collected=sum(entry['availability']=='available' for entry in allowed)+len(fixed)-2
    assert f'Downloaded requi-everything.zip: {collected} of {total} sections collected; unavailable: '+', '.join(missing)+'.' in text,text
    assert "===== Known omissions =====" in body
    assert "===== Server logs (last 30 min) =====" in body
    assert '"error"' in body  # the failed section is inline, not silently dropped
    assert "===== Client logs (this tab) =====" in body
    page.locator(".debug-dialog .dialog-x").click()


def test_copy_summary_uses_the_repository_build_identity(page, ui):
    page.route('**/api/tasks/'+page._debug_task_id+'/diagnostics', lambda route: route.fulfill(
        status=200, content_type="application/json", body='{"question":"q","control_state":{}}'))
    _open_debug(page, ui)
    page.locator("#copysummary").click()
    page.wait_for_function(
        "() => document.querySelector('#debugstatus').textContent.startsWith('Diagnostic summary copied')",
        timeout=10000)
    copied = page.evaluate("navigator.clipboard.readText()")
    assert f"build: {LABEL}" in copied
    page.locator(".debug-dialog .dialog-x").click()
