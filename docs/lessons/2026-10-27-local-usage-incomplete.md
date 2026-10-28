# Rejected usage rows make a local total incomplete

Malformed JSON and non-object JSON were silently omitted. Count rejects while
preserving source bytes, warn without payload/path and expose completeness on
local cost evidence/rollups. The header must not present a partial local total as
current. Keep the valid-row calculation, ledger charges and cloud accounting
unchanged. Empty first-run files are not corrupt. Rollup write failure leaves a
safe warning rather than disappearing. Regression tests were failing first.
