# Engineering rules

## Before changing code
- Analyze the failure and instrument the real path before fixing it.
- Fix causes in code, not symptoms or prompts.
- Search for a shared component/helper first; use it instead of a one-off.
- If no shared helper fits and a second use is plausible, build the shared one first.
- No bespoke copies of dialogs, prompts, spinners or backend helpers; reviewers/tests flag them.
- Consolidation should reduce net code, not add another competing path.
- Prefer deterministic rules over LLM calls.
- Prefer warnings to errors unless money or data safety requires stopping.
- Never silently fall back, retry a paid call, or rewrite approved work.

## Checks and acceptance
- Fail fast: cheapest, most likely failures first; print the reason, exception and next step without secrets.
- A passing check on a broken app is a test gap, not success.
- Keep tests hermetic: disposable databases, stubbed external services, no live accounts or models.
- Dry-test money-costing paths before real use; no paid run without explicit approval.
- Green tests are not acceptance: exercise the real user journey, a fresh database and zero credits.
- Keep local execution close to production; do not add preview-only bypasses.
- Preserve tenant/session boundaries, accounting evidence and approved work during repairs.

## Operations
- Put repeatable steps in repo scripts; pull current code and fail loudly on missing environment.
- Read provider keys from the repo .env first; prompt with hidden input only for missing keys.
- Keep other protected settings file-first; never print secrets or source dotenv as shell code.
- Keep one launcher with flags, not separate launch paths.
- Color console output by explicit log severity, never by words in a line.
- Make the prompt a complete visible last line before waiting for input.
- Show progress during long work; silence must not look like a hang.

## UI
- Use shared components and consistent type, spacing and action styles.
- Label fields above inputs, group related fields, and show errors beside the action.
- Confirm deletion; disabled buttons must explain why.
- Give actions busy, success and error states; use loading text with motion, never a bare spinner.
- Dialogs share focus handling, Enter submission and Esc close; gates default to Approve without changing approval meaning.
- Show customer prices in credits with USD equivalents where applicable; vendor costs stay operator-only.
- Never put operator links, private operations terms or raw readiness errors in customer pages, even for admins.
- Keep saved artifacts and audit evidence accessible; Delete means confirmed Stop/hide, not erasing accounting.

## Pull requests
- Lead with the problem, then fix, scope and test evidence; no diffstats.
- Prove the exact current head/base and full CI before calling a PR ready.
- The owner reviews and merges; do not infer completion from silence.
