# Numeric call evidence and full task accounting exports

## Problem and approved scope
Token counts were lost at three redaction layers and also explicitly suppressed
by the cloud diagnostics policy. Everything reused a newest-20 account ledger
which could not explain all the calls of one answer. Numeric counts, rates and
recorded costs are diagnostic data, not credentials.

## Changes
- Exact finite, nonnegative numeric metrics are preserved in server and browser
  JSON/text redaction. Boolean, string, object, negative and nonfinite values are
  not exempt. Credential-looking keys remain redacted. identity uses the same
  JSON scrubber. Structured accounting join IDs survive the browser scrubber.
- Cloud model timing diagnostics expose numeric usage and count/rate fields.
  The hidden newest-300 model-call tail is removed for this recorded source.
- A SELECT-only owner-task endpoint pages fixed accounting projections by stable
  immutable IDs: answer receipts, final answer usage, provider attempts and
  retail contributions. No arbitrary query or payload export. Every page first
  checks the task owner under the normal RLS transaction.
- Everything collects every page per source, preserves partial results and marks
  failures unavailable. It states the live-capture scope: sources can change
  during collection, so this is not a cross-source point-in-time snapshot.
- Answer usage joins its contribution using the exact settlement key formula.
  Provider attempts use the exact attempt contribution key. No order, timestamp,
  token-count or same-model matching is used to manufacture a call join.
- Exact decimal amounts are strings. Retail credits and provider expense are
  separate. Expense USD uses a recorded frozen scale only; missing scale/amount
  stays unknown. Receipt actual replaces estimate via correction; display
  contributions are not extra debits.

## Honest historical limits
Answer-v1's final usage contains attempt ID, phase and token counts, but does not
persist logical call/field IDs, per-call timing, model pin or provider expense.
These remain null with provenance, even when a separate model log has timing and
model names. Future full per-call attribution needs additional captured metadata.
Nothing in this PR reconstructs that missing history or prices it at today's
rates. Existing retained model logs remain a separate unjoined source. The
ordinary account ledger UI remains a recent tail; the new Everything section is
full selected-task accounting.

## Hermetic proof
Failing numeric-redaction tests preceded the fix. Real signed Postgres tests
cover 53 contributions across pages, no duplicates, foreign-owner denial and an
actual final answer settlement's contribution key/posted amount. Real Chromium
opens Debug, clicks Download everything ZIP and inspects the downloaded ZIP for
numeric usage, exact IDs, retail amount and null unknown fields. Hostile numeric
values and secret keys remain redacted. Partial collector failure preserves
other sources. No provider call, paid execution, live DB/schema change, owner
process operation, deployment or merge is performed.

Local gates: 2473 fast tests passed (1023 + 1450), one skip and five marker
exclusions. Focused final export/accounting/browser suite: 80 passed. Hosted CI
is unverified because its budget is exhausted; local-only gate as agreed.
