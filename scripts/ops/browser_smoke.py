#!/usr/bin/env python3
"""Browser smoke for the exec preview: the real page must boot clean for an
anonymous visitor - and an anonymous boot is NOT error-free by design.

In cloud mode the tenant boundary answers 401 to the page's own pre-sign-in
probes (the balance widget and the workspace list). Anonymous telemetry POST
must succeed. Chromium
echoes each 401 to the console as "Failed to load resource: ... 401" and the
page's own clientLog echoes each failed api() call as a console error. A gate
that fails on any console error therefore aborts every healthy anonymous
preview (2026-10-27: exactly two such console lines killed Shy's run at step 9).

This smoke classifies instead of counting:
- every HTTP response >= 400 must be a 401 on one of the page's own expected
  anonymous probe endpoints, named below - anything else fails WITH its URL
  (Chromium's console line omits the URL, so responses are the source of truth)
- Chromium's resource-load console lines carry no URL and are dropped when a
  captured response carries the same status (the response above already
  classified it, by URL)
- the page's own api.error telemetry echo is tolerated only when its structured
  detail names an expected anonymous 401 endpoint
- a request Chromium reports as failed is tolerated only when a response was
  captured for that same request: headless Chromium misreports COMPLETED
  fetch() calls as net::ERR_ABORTED (the response event fires, the page's
  fetch resolves 204, and requestfailed fires anyway - reproduced 2026-11-09;
  XMLHttpRequest and document loads report correctly). The fire-and-forget
  client-log POST always lands its 204 and still prints as a phantom failure,
  so counting requestfailed kills every healthy boot (exactly the two lines
  in Shy's browser-smoke.log).
- every other console error, every pageerror, every genuinely failed request
  (no captured response), and a boot stuck on "Loading workspace..." or
  rendering nothing FAILS the smoke

Anonymous and read-only BY CONSTRUCTION: the preview runs on the live database
with real sign-in, so this never submits a form and never signs in. The
fake-login half lives in tests/test_ui_playwright.py against a throwaway
database.
"""
import re
import sys
from urllib.parse import urlsplit

# The page's own anonymous probes in cloud mode (ground truth captured from the
# real page boot, pinned by tests/test_preview_browser_smoke.py). 401 only:
# any other status on these paths is a failure like any other.
EXPECTED_ANONYMOUS_401 = frozenset({
    ("GET", "/api/account/credits"),  # the header balance widget
    ("GET", "/api/tasks"),            # the workspace list probe
})

_RESOURCE_LOAD = re.compile(r"Failed to load resource: the server responded with a status of (\d+)")


def classify(events):
    """-> (failures, tolerated): every entry names what it talks about."""
    failures, tolerated = [], []
    if events.get("telemetry_success") is False:
        failures.append("no successful anonymous POST /api/client-log captured")
    statuses = {r["status"] for r in events["responses"]}
    for r in events["responses"]:
        path = urlsplit(r["url"]).path
        if r["status"] == 401 and (r["method"], path) in EXPECTED_ANONYMOUS_401:
            tolerated.append(f"anonymous 401 {r['method']} {path} (expected: the page probes it before sign-in)")
        else:
            failures.append(f"HTTP {r['status']} {r['method']} {r['url']}")
    for f in events["request_failures"]:
        line = f"request failed: {f['method']} {f['url']} - {f['failure']}"
        if f.get("answered"):
            tolerated.append(f"{line} (a response was captured for this same request - Chromium misreports completed fetch calls as failed)")
            continue
        failures.append(line)
    for c in events["console"]:
        text = c["text"]
        m = _RESOURCE_LOAD.match(text)
        if m:
            if int(m.group(1)) in statuses:
                continue  # the response event above already classified this, with its URL
            failures.append(f"console reports a failed resource load (status {m.group(1)}) that no captured response explains: {text}")
            continue
        detail = c.get("detail") or {}
        endpoint, status = detail.get("endpoint"), detail.get("http_status")
        if endpoint and status == 401 and any(endpoint == path for _, path in EXPECTED_ANONYMOUS_401):
            tolerated.append(f"the page logged its own expected anonymous 401 on {endpoint}")
            continue
        failures.append(f"console error: {text}")
    for e in events["pageerrors"]:
        failures.append(f"pageerror: {e}")
    if events["boot"] == "stuck":
        failures.append('boot never left "Loading workspace..."')
    elif events["boot"] == "empty":
        failures.append("boot left the loading state but rendered nothing")
    # Collapse repeats of the same tolerated line (the probes poll).
    collapsed, seen = [], {}
    for t in tolerated:
        seen[t] = seen.get(t, 0) + 1
    for t, n in seen.items():
        collapsed.append(t if n == 1 else f"{t} x{n}")
    return failures, collapsed


def collect(base, boot_timeout_ms=20000, settle_ms=2000):
    """Drive the real page in headless Chromium. Returns None when Chromium is
    not installed (caller prints the install hint instead of failing)."""
    from playwright.sync_api import sync_playwright
    events = {"responses": [], "console": [], "pageerrors": [], "request_failures": [], "boot": "unknown", "telemetry_success": False}

    def on_console(m):
        if m.type != "error":
            return
        detail = None
        try:
            if len(m.args) > 1:
                detail = m.args[1].json_value()
        except Exception:
            detail = None
        events["console"].append({"text": m.text, "detail": detail if isinstance(detail, dict) else None})

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as e:
            if "Executable" in str(e):
                return None
            raise
        page = browser.new_page()
        answered_request_ids = set()
        def on_response(r):
            answered_request_ids.add(id(r.request))
            if r.request.method == "POST" and urlsplit(r.url).path == "/api/client-log" and 200 <= r.status < 300:
                events["telemetry_success"] = True
            if r.status >= 400:
                events["responses"].append({"method": r.request.method, "url": r.url, "status": r.status})
        page.on("response", on_response)
        page.on("requestfailed", lambda r: events["request_failures"].append(
            {"method": r.method, "url": r.url, "failure": str(r.failure), "_rid": id(r)}))
        page.on("console", on_console)
        page.on("pageerror", lambda e: events["pageerrors"].append(str(e)))
        page.goto(base + "/")
        try:
            page.wait_for_function("!document.body.innerText.includes('Loading workspace')", timeout=boot_timeout_ms)
            events["boot"] = "ok"
        except Exception:
            events["boot"] = "stuck"
        if events["boot"] == "ok":
            if not page.locator("#app").inner_text().strip():
                events["boot"] = "empty"
            # Let the first post-boot poll round fire: a widget that errors only
            # on its first poll still shows up before the verdict.
            page.wait_for_timeout(settle_ms)
        browser.close()
    # Chromium reports some COMPLETED requests as failed (see the module
    # docstring). The response listener is the source of truth: a failure line
    # whose request already produced a captured response is a misreport, not a
    # network failure. Both events land inside the collection window, so the
    # verdict is decided here, after close.
    for f in events["request_failures"]:
        f["answered"] = f.pop("_rid") in answered_request_ids
    return events


def main():
    base = sys.argv[1]
    events = collect(base)
    if events is None:
        # No browser ran, so the boot state is UNKNOWN, never "failed". The log
        # must name the repair itself - the preview caller only relays this output.
        print("Chromium is not installed for " + sys.executable + "; no browser ran and the boot is unverified.")
        print("Install it once for this exact interpreter: " + sys.executable + " -m playwright install chromium")
        sys.exit(2)
    failures, tolerated = classify(events)
    for t in tolerated:
        print("tolerated:", t)
    if failures:
        print("browser errors during boot:")
        for f in failures:
            print(" ", f)
        sys.exit(1)
    print("browser smoke: page boots clean - every console/network error above is a named, expected anonymous 401")


if __name__ == "__main__":
    main()
