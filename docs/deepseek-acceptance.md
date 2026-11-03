# Owner-only repeated acceptance

Ordinary users never run this checklist or these commands. It is an operator release/activation check, repeated after provider-path changes, a new pin or before a paid acceptance run. Normal users choose a model and Start; the service applies all tenant, admission and money checks automatically.

The repo owns the implementation. From the checkout run:

```sh
scripts/ops/acceptance.sh --dry-run
```

`req acceptance --dry-run` is a thin convenience route to that same repo entry. `req preflight --email YOUR_SIGNED_UP_EMAIL` runs the existing read-only owner policy evidence and this same acceptance readiness check. No new independent policy checker is installed.

The script loads private preview settings and repo `.env`, then uses the configured app origin or the preview default `http://127.0.0.1:8001`. `--base-url` selects an explicit origin. It prints local preview vs cloud and the origin. Before credentials or execution, the service must identify correctly. It reuses a configured private acceptance token or `PREVIEW_BROWSER_STATE` cookies for that exact host; otherwise it asks with hidden input for only the missing existing owner access token. It does not save it, create a login, or make you edit config. Back office has no stored token for this script to reuse automatically.

Dry mode makes GET requests only, no task writes and no provider calls. It checks session, marker migration/owner permission, approved default pin and positive server ceiling. With `--max-usd N` it also checks the server ceiling is at most N. It prints a planning scenario from the actual reviewed pin:20,000input tokens,6,000output tokens and60seconds active compute, separately providerUSDandretailcredits/USD. This is not a measured forecast or a maximum.

After separately approving the exact run ceiling, the owner runs:

```sh
scripts/ops/acceptance.sh --execute --max-usd APPROVED_USD
```

Replace APPROVED_USD with the approved number. First apply the owner test-task migration, review the default pin, and set the server task-generation ceiling at or below that number. The script never changes it. An unidentified target refuses. Explicit remote targets require HTTPS.

Execution creates a marked test, starts once, answers one text question, waits for another, proves live progress and visible cost in a browser, and purges only with complete settled cost/usage. No blind structured approvals or automatic retry. Failure keeps evidence and tries to save a private Debug ZIP. Successful purge preserves money/audit and charges still count in balances and spend limits. Private results are in ~/Downloads/requi-acceptance.
