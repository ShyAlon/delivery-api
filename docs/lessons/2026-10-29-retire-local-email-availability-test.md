# Remove local email-availability test only (2026-10-29)

Owner11-test cleanup,theme4 removes test_basic_email_validation_and_availability
and unused SQLiteStore import:9test lines deleted. No production changes.
Only direct local API invalid-email400 and availability true/false assertions
found are removed. email_availability/basic_signup/local frontend probe remain;
local browser still checks format/availability/signup. Cloud404refusal and
no-probe/confirmation UI test remain;Supabase signup/refusal tests unchanged.
No cloud behavior in removed test,so no split. Before6passed1.65s;one-off absence
red then green,no committed guard. After113passed0skips8.18s across auth/API/cloud/
bootstrap/Supabase identity+sessions/local refresh/control plane/retail ledger.
Hosted CI/full suite/browser rerun unrun. Endpoint deletion needs separate OK.
