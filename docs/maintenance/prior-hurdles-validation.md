# No-spend prior-hurdles validation

From the delivery-api checkout, with the local preview running:

```bash
.venv/bin/python scripts/ops/validate-prior-hurdles.py
```

Default preview is http://127.0.0.1:8001; `PREVIEW_EXEC_PORT` or `PREVIEW_URL` may select another loopback port. Prerequisites are the repo dev dependencies, installed Playwright Chromium and installed sibling interview-engine/spec-generator. Missing dependencies fail loudly. No live database credentials are accepted. Allow roughly one to three minutes on a laptop; fixture migrations and browser starts dominate.

The script prints PASS, FAIL or NOT CHECKED for each hurdle and keeps private logs/screenshots/ZIPs in the output folder it prints. No skipped fixture counts as a pass. Exit0 means fixture checks and anonymous preview checks passed; optional signed-in checks can still be NOT CHECKED.

| Hurdle | Automated evidence | Expected |
| --- | --- | --- |
| Account Debug ZIP | Actual account-menu button, real local PG/account registry, ZIP contents/redaction, denied account and revocation | ZIP downloads for configured account; denied after revocation and for other account |
| Stop router404 | Real PG engine Stop epoch and installed control router, no model/HTTP provider | Ordinary interview Stop stays on normal router; a different uninstalled revision remains denied |
| Own-test transition | Real disposable PG transaction and existing transition function | Dry run changes nothing; apply is atomic/idempotent; active work still refused; confirmed hidden retained job does not block |
| Answer/question pairing | Real task UI with scripted delayed response/SSE ordering | Q1 answer remains paired with Q1; Q2 prompt never receives Q1 answer |
| Final package | Real completion button, package create/download routes and zero-model producer; tenant/revision/tamper tests | ZIP downloads; task/owner/revision match; member hashes match; stale/tampered/foreign content refused |
| Worker Jobs Stop/Delete | Real Back office UI/proxy with scripted upstream plus real disposable PG API/store checks | Stop sends hide:false; Delete sends hide:true; accounting retained; no fake worker exit |
| Running preview | Anonymous health GET and real Chromium boot | Health ok; only known anonymous401 probes tolerated; unexpected errors fail |

None of the fixture outcomes is presented as a signed-in check of the running preview. The script does not create or mutate real preview tasks, change own-test settings, start/retry generation, submit answers, create a final package, or Stop/Delete live tasks.

## Optional signed-in preview check

No prerequisite for the main validation run. To check an existing account's actual Debug ZIP too, create a private local browser state once:

```bash
(umask 077; .venv/bin/python -m playwright codegen --save-storage="$HOME/preview-browser-state.json" http://127.0.0.1:8001)
```

Sign in locally in that window, then close it. The file contains login cookies. Keep it private, never attach it or paste it into chat. Run:

```bash
PREVIEW_BROWSER_STATE="$HOME/preview-browser-state.json" .venv/bin/python scripts/ops/validate-prior-hurdles.py
```

Missing state means NOT CHECKED, not failure. An explicitly configured missing/insecure/expired state fails. An account without debug.read or without an existing task linked to a run is NOT CHECKED with a reason. Existing accounts are not granted new permissions. This optional check only navigates/reads and downloads through the account-menu Debug button; mutating UI requests are blocked.
