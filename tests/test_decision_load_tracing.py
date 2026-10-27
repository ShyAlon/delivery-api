"""Deterministic tests for the decision-page load tracing (obs/decision-load-tracing).

One live decision is produced once; every case then loads the SAME decision URL
with a deterministic network/payload condition and asserts the exact trace records
emitted over the real client-log transport (captured in the page, hermetically).
No production semantics are changed by the instrumentation: fetch timeout, retry
and navigation behavior are untouched; the pending observation is a log-only timer.
"""
import json
import os
import time

import test_crosshost_interview_acceptance as X
from test_crosshost_interview_acceptance import dsn, keys  # reuse module fixtures

# Same prerequisites as the acceptance module it reuses: without the engine and
# generator source checkouts it must skip, not crash on a None path.
pytestmark = X.pytestmark

CANARY_BRIEF = 'A SaaS web app for surf schools. CANARYBRIEF-ZQXWV must never appear in logs.'
ALLOWED_DETAIL_KEYS = {'wait_id', 'revision', 'step', 'epoch', 'outcome', 'duration_ms',
                       'request_ref', 'error_kind', 'form_present', 'radios', 'branch',
                       'reason', 'after_ms', 'current_epoch', 'pattern'}


def test_decision_load_tracing(dsn, keys, tmp_path_factory):
    from playwright.sync_api import sync_playwright
    root = tmp_path_factory.mktemp('load-tracing')
    host = X.AdmittedHost('local', root, keys, dsn)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        # One-shot browser-side delay for the overlap case: first decision GET delayed N seconds.
        page.add_init_script("(()=>{const of=window.fetch;window.fetch=(u,o)=>{try{const d=Number(sessionStorage.getItem('__decDelayOnce')||0);if(d&&String(u).includes('/decisions/')){sessionStorage.setItem('__decDelayOnce','0');return of(u,o).then(r=>new Promise(res=>setTimeout(()=>res(r),d*1000)))}}catch(_){}return of(u,o)}})()")
        # Browser-side delay wrapper for the interleave case: inert unless the page sets sessionStorage.__delayDec.
        page.add_init_script("(()=>{const of=window.fetch;window.fetch=(u,o)=>{try{if(sessionStorage.getItem('__delayDec')&&String(u).includes('/decisions/'))return new Promise(res=>setTimeout(()=>res(of(u,o)),2000))}catch(_){}return of(u,o)}})()")
        records = []
        page.route('**/api/client-log',
                   lambda r: (records.append(r.request.post_data_json), r.fulfill(status=204)))
        decision_refs = []
        page.on('response', lambda resp: decision_refs.append(resp.headers.get('x-request-id'))
                if '/api/' in resp.url and '/decisions/' in resp.url else None)
        X.sign_in(page, host.base)
        task_id = X.create_task(page, host.base, 'Surf school booking', CANARY_BRIEF)
        page.locator('#startengine').click()
        answers = ['Customers pick a slot and pay online.', 'They pay by card when they book.',
                   'Rainy days are automatically refunded.', 'Owners see a daily roster.',
                   'Instructors confirm from their phone.', 'Weekly payouts are automatic.']
        X.pump_interview(page, host.base, task_id, answers, finish=False, timeout=120)
        page.wait_for_selector('#opendecision', timeout=15000)
        href = page.locator('#opendecision').get_attribute('href')
        real = page.request.get(host.base + '/api' + href).json()
        pat = '**/api/tasks/*/decisions/**'

        def outcomes(kind='decision.outcome'):
            return [r['detail'] for r in records if r.get('kind') == kind]

        def kinds():
            return [r.get('kind') for r in records]

        # C1: committed - real load, correlated to its request reference
        mark = len(records)
        page.goto(host.base + href)
        page.wait_for_selector('#reviewform', timeout=15000)
        page.wait_for_timeout(500)
        outs = outcomes()[len([r for r in records[:mark] if r.get('kind') == 'decision.outcome']):]
        assert len(outs) == 1, outs
        assert outs[0]['outcome'] == 'committed' and outs[0]['form_present'] == 1
        assert outs[0]['request_ref'] and outs[0]['request_ref'] in decision_refs
        assert 'decision.pending' not in kinds()[mark:]

        # C2: connection-level failure -> fetch_error/network, existing notice preserved
        page.route(pat, lambda r: r.abort())
        page.goto(host.base + href)
        page.wait_for_timeout(3000)
        page.unroute(pat)
        outs = outcomes()
        assert outs[-1]['outcome'] == 'fetch_error' and outs[-1]['error_kind'] == 'network', outs[-1]
        assert outs[-1]['request_ref'] is None, outs[-1]  # request never reached the server: no invented ref
        assert 'Review unavailable' in page.locator('#app').inner_text()

        # C3: HTTP 500 -> fetch_error/http_500
        page.route(pat, lambda r: r.fulfill(status=500, content_type='application/json',
                                            headers={'x-request-id': 'ref-injected-500'},
                                            body=json.dumps({'detail': 'injected'})))
        page.goto(host.base + href)
        page.wait_for_timeout(3000)
        page.unroute(pat)
        outs = outcomes()
        assert outs[-1]['outcome'] == 'fetch_error' and outs[-1]['error_kind'] == 'http_500', outs[-1]
        assert outs[-1]['request_ref'] == 'ref-injected-500', outs[-1]  # HTTP error keeps its own ref

        # C4: typed evidence missing -> typed_evidence_missing + existing error modal
        bad = json.loads(json.dumps(real))
        bad.get('review', {}).get('decision', {}).get('context', {}).pop('evidence', None)
        page.route(pat, lambda r: r.fulfill(status=200, content_type='application/json', body=json.dumps(bad)))
        page.goto(host.base + href)
        page.wait_for_timeout(3000)
        page.unroute(pat)
        outs = outcomes()
        assert outs[-1]['outcome'] == 'typed_evidence_missing', outs[-1]
        assert 'Something needs attention' in page.locator('#modal').inner_text()
        assert 'Typed review evidence is unavailable' in page.locator('#modal').inner_text()  # tracing did not mask the original error

        # C5: render-breaking payload -> render_error with error class only (no raw strings)
        bad2 = json.loads(json.dumps(real))
        bad2.get('review', {}).get('decision', {}).get('context', {}).get('evidence', {}).pop('conops_draft', None)
        page.route(pat, lambda r: r.fulfill(status=200, content_type='application/json', body=json.dumps(bad2)))
        page.goto(host.base + href)
        page.wait_for_timeout(3000)
        page.unroute(pat)
        outs = outcomes()
        assert outs[-1]['outcome'] == 'render_error', outs[-1]
        assert outs[-1]['error_kind'].startswith('js_'), outs[-1]

        # C6: SPA navigation during the fetch -> stale_silent + route superseded, both now visible
        import threading
        def delayed(route):
            def late():
                try:
                    route.fulfill(status=200, content_type='application/json', body=json.dumps(real))
                except Exception:
                    pass  # the page navigated away first: the request was aborted, as intended
            threading.Timer(2.5, late).start()
        page.route(pat, delayed)
        page.goto(host.base + href)
        page.wait_for_selector('a[href="/"]', timeout=10000)
        page.locator('a[href="/"]').first.click()
        page.wait_for_timeout(4500)
        page.unroute(pat)
        outs = outcomes()
        assert outs[-1]['outcome'] == 'stale_silent', outs[-1]
        assert any(r.get('kind') == 'route.outcome' and 'superseded' in r.get('message', '')
                   for r in records), kinds()

        # C6b: interleaved requests during the fetch cannot steal the correlation ref
        page.evaluate("sessionStorage.setItem('__delayDec','1')")
        page.goto(host.base + href)
        refs_before = len(decision_refs)
        page.wait_for_timeout(700)
        page.evaluate("()=>{window.__ix=fetch('/api/tasks/" + task_id + "').then(r=>r.status)}")  # unrelated in-flight request, not awaited
        page.wait_for_selector('#reviewform', timeout=15000)
        page.wait_for_timeout(400)
        page.evaluate("sessionStorage.removeItem('__delayDec')")
        outs = outcomes()
        assert outs[-1]['outcome'] == 'committed', outs[-1]
        assert len(decision_refs) > refs_before
        assert outs[-1]['request_ref'] == decision_refs[-1], (outs[-1], decision_refs[-1])
        assert page.evaluate('window.__ix') == 200  # the interleaved request really completed

        # C7: slow load -> one decision.pending, then exactly one terminal outcome; no semantic change
        def slow(route):
            time.sleep(12)
            route.fulfill(status=200, content_type='application/json', body=json.dumps(real))
        mark = len(records)
        page.route(pat, slow)
        page.goto(host.base + href)
        page.wait_for_selector('#reviewform', timeout=30000)
        page.wait_for_timeout(500)
        page.unroute(pat)
        later = records[mark:]
        pend = [r for r in later if r.get('kind') == 'decision.pending']
        outs = [r['detail'] for r in later if r.get('kind') == 'decision.outcome']
        assert len(pend) == 1 and pend[0]['detail']['after_ms'] == 10000, pend
        assert len(outs) == 1 and outs[0]['outcome'] == 'committed', outs

        # C10: two overlapping loads of the SAME decision URL, reversed completion - each keeps its own ref
        page.evaluate("sessionStorage.setItem('__decDelayOnce','3')")
        calls = {'n': 0}
        def mixed(route):
            calls['n'] += 1
            if calls['n'] == 1:
                route.continue_()  # load A: real server response, delayed 3s in the browser
            else:
                route.fulfill(status=500, content_type='application/json',
                              headers={'x-request-id': 'ref-interleaved-500'},
                              body=json.dumps({'detail': 'injected overlap 500'}))
        page.route(pat, mixed)
        mark = len(records)
        page.goto(host.base + href)
        page.wait_for_timeout(700)  # load A's fetch is now in flight (delayed)
        page.evaluate("window.dispatchEvent(new PopStateEvent('popstate'))")  # same URL: second dispatch, load B
        page.wait_for_timeout(5000)
        page.unroute(pat)
        later = [r['detail'] for r in records[mark:] if r.get('kind') == 'decision.outcome']
        stale = [o for o in later if o['outcome'] == 'stale_silent']
        err = [o for o in later if o['outcome'] == 'fetch_error']
        assert len(stale) == 1 and len(err) == 1, later
        # Load A's response WAS processed (server replied before the abort), so it carries its OWN
        # server ref; load B carries the injected 500's ref. The point: no cross-association either way.
        assert stale[0]['request_ref'] and stale[0]['request_ref'] in decision_refs, stale[0]
        assert stale[0]['request_ref'] != 'ref-interleaved-500', stale[0]
        assert err[0]['request_ref'] == 'ref-interleaved-500', err[0]  # the 500 load keeps its own ref
        assert stale[0]['epoch'] != err[0]['epoch'], later  # two distinct loads

        # C8: privacy - no content/credentials on the transport; detail keys are metadata only
        blob = json.dumps(records)
        assert 'CANARYBRIEF-ZQXWV' not in blob
        assert 'eyJ' not in blob and 'Bearer' not in blob and 'cookie' not in blob.lower()
        for r in records:
            if r.get('kind', '').startswith(('decision.', 'route.')):
                assert set((r.get('detail') or {}).keys()) <= ALLOWED_DETAIL_KEYS, r

        # C9: scrub canaries - the existing scrub layer still redacts secrets before export
        scrubbed = page.evaluate(
            "(async()=>{const m=await import('/assets/log-scrub.js');"
            "return m.scrubText('Bearer abcdef1234567890 eyJABCDEFGH.IJKLMNOPQ.RSTUVW "
            "?token=seekrit mail founder@example.com');})()")
        assert 'abcdef1234567890' not in scrubbed and 'seekrit' not in scrubbed
        assert 'founder@example.com' not in scrubbed and '[jwt]' in scrubbed, scrubbed
        browser.close()
