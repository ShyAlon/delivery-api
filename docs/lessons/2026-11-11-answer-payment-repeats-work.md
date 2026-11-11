# 2026-11-11: make the answer the payment unit

## Problem
Per-provider admission, account and settlement trips dominate an answer. The owner selected an adjustable0.1-credit debit before processing and one actual-consumption correction afterward. Precision at every call is not the goal. No new Q/call/retry/deadline gate or dispatched journal belongs in the new path.

## Foundation
Freeze configured start credits and current retail rates once. Start checks sufficient balance and debits E atomically. End prices measured usage, advances the account fractional remainder and debits A-E (or refunds E-A) once. Overrun may make balance negative; it is recorded, not refused. Final attempt uniqueness prevents two answers charging the same call, without a pre-send marker.

Tests failed first with the pricing module absent and DB functions absent. Restricted-role isolated PG tests then proved same-input replay, concurrent admission/correction, config edits not repricing old receipts, cross-owner/browser/revoked-session denial, existing ledger attribution and actual overrun. Local libpq trace counts two Sync barriers including identity/role/BEGIN/COMMIT. It is not production-pooler/full-answer performance evidence.

## What not to claim
This additive migration and pure module do not activate a runtime path. Job enqueue/claim/input identity, per-attempt bypass, bulk retail display projections, publication CAS, summary logging and legacy/new cutover are subsequent integration. Current functions require an already valid owned job; they do not create or claim one. No endpoint calls them. Production schema application is owner-run, not a test side effect. Stats include all receipts, not just overruns; missing actual remains unknown with E retained.
