# Owner-run DeepSeek acceptance

This script is not paid acceptance evidence until you explicitly approve a provider-cost ceiling and run it. It has not been run against a paid provider by the implementer.

Prerequisites:
- Merge the test-marker/purge PR and apply its migration yourself.
- Use the real running app with the provider HTTP fixes and real child runtime installed.
- Review the default DeepSeek model pin, set a positive operator task-generation ceiling, and keep that server ceiling at or below the amount you explicitly approve for this run.
- Install the repo's dev dependencies and Playwright Chromium.
- Put `DELIVERY_BASE_URL` and either `DELIVERY_ACCEPTANCE_TOKEN` or `DELIVERY_ACCEPTANCE_EMAIL` / `DELIVERY_ACCEPTANCE_PASSWORD` in the repo's private `.env` or private settings file. No secret belongs in the command line. Missing execution login details are requested with hidden terminal input. The existing server's provider key is used; this script never sends a new provider key or changes server configuration.

Read-only preflight (zero task writes and zero provider calls):

```sh
.venv/bin/python scripts/owner/deepseek-acceptance.py --dry-run
```

After separate approval, the owner runs one line, replacing `APPROVED_USD` with that approved limit:

```sh
.venv/bin/python scripts/owner/deepseek-acceptance.py --execute --max-usd APPROVED_USD
```

The script signs in through the real API, checks the owner test view (including DB/migration permission), reviewed pin and operator ceiling before creating anything. Dry mode uses an existing token and only GET requests. It reports a price scenario from the actual approved pin, not remembered vendor prices: 20,000 input tokens, 6,000 output tokens and 60 seconds active compute. The scenario is a planning allowance, not an expected measured bill or a maximum. Graph calls and prompt sizes vary; the server ceiling is the provider-spend control. Retail credits include measured compute and can differ from vendor expense.

Execution creates an immutable test task, starts DeepSeek once, waits for a text question, submits one fixed fixture answer, and waits for a new text question. It does not approve arbitrary structured decisions. A real browser checks the question, live persisted Start progress and cost visibility, then captures a screenshot. Cost/usage must be complete before the guarded purge is called. The purge is atomic and refuses live or unsettled work. No automatic retry. Failure retains the test and tries to save a private Debug ZIP. If ZIP collection fails, the report says `debug_zip: null`; use the retained task to download evidence manually.

Outputs go to `~/Downloads/requi-acceptance` with private directory/file permissions: datetime with timezone, model and pin revision, elapsed time, actual retail credits and USD, normalized usage, screenshot, and result JSON. Actual provider expense is summed from settled attempt evidence with verified scales, never inferred from retail price. Money/audit records survive and charges still count in balances and spend limits.

Limits: the dry preflight cannot prove the deployed server's HTTP Content-Type implementation. The strict HTTP fixtures are run separately in CI. The script refuses if it cannot observe live Start progress while working, even if a fast run otherwise returns a question. No paid execution or live DB mutation was performed during implementation.
