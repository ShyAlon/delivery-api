# Debug is an account capability, not an environment

The avatar offered Download debug ZIP on the private-account runtime but its endpoint always returned 503 under CLOUD_TENANTS. Download everything ZIP was a different browser-built export and did work. The two buttons hid different privacy decisions behind an environment switch.

Raw debug now requires the existing explicit debug.read principal, active authenticated session and matching enabled database operator registry. The admin badge, email string, local mode and debug query flag do not grant access. No account is automatically provisioned and no live registry is changed. The configured owner's existing debug.read grant is reused.

Removed environment refusal and payload suppression from the provider debug ZIP and execution view, and the question-quality read gate. Operator-reader installation and retrieval no longer depend on tenant mode. The browser gets a freshly verified debug_features boolean and uses it for the raw ZIP button, execution collection and settings read. Existing independent write/admin controls remain; reading debug does not grant settings edits, account changes or spending.

PG-native runs keep provider evidence in owned workflow documents. The exporter reads those exact hash-verified bytes instead of an empty local directory. Missing evidence is listed, never reconstructed. Debug export reads retained task state rather than depending on a successful live engine snapshot, so a broken task GET does not prevent diagnosis. ZIP secret redaction, task scope and bounds remain.

Tests first reproduced the 503 for an authorized account and the absence of account-based refusal. Added actual account+registry route tests, disabled-registry denial, non-operator denial, admin-badge non-authority, both environment flag values and real browser ZIP download. Question-quality fixtures now explicitly seed/clean a disposable debug.read principal; original behavior assertions are unchanged. No assertion was weakened or removed. Existing bounded export, collector, operator and browser contracts still run.

52 selected tests passed. Browser pixels inspected: enabled Download debug ZIP, successful download status, legible dialog. Hosted CI, live account configuration and deployed topology remain unverified. No live DB effects, deploy or paid call.
