# Confirmed provider expense admission

Preparation only. Public execution remains disabled. No live policy, migration,
provider, SMTP account, or recipient is installed by this change.

The private launch gateway checks the database guard immediately before marking
an attempt dispatched. A shared platform lock serializes that check with provider
settlement. Existing owner locks still enforce three concurrent calls. Launch
validation rejects owner quotas, per-attempt caps and attempt-rate limits.

The guard counts confirmed settled provider charges only:
- Session: all confirmed charges for the owner/session binding.
- Platform: confirmed settlements in the trailing 24 hours, against the reviewed
  per-registered-user amount times the current auth.users count.
- Unknown outcomes: stop new platform admissions until reviewed, without guessing
  a charge. Reserved and dispatched calls do not count as confirmed expenses.

Accepted calls finish and settle even after a threshold is reached. Exact debit
and breach episode/outbox evidence commit in the same transaction. Replays do not
charge again or send duplicate episodes. A platform threshold that rolls off may
start a new episode on a later breach. Email failure does not reopen admission.

The narrow database definer checks the authenticated session and an owned running
job before reading cross-owner totals. It returns only a reason code. Browser
roles have no access to the function, policy, episodes or provider ledger. It does
not grant operator identity or cross-owner browsing access.

Deployment still needs explicit reviewed price/expense scales, policy amounts,
restricted credentials and live migration/Auth validation. Alert delivery is an
outbox only. SMTP transport, recipient approval, retries and recovery need their
own validation before the production fence can open. A crashed dispatched call
must be classified uncertain during explicit recovery; it cannot be treated as a
free cancelled call.
