# Capture future call facts without reconstructing history

## Problem and boundary

The Everything export already contains full selected-task accounting history, but old answer settlement rows never recorded a logical call, requested field, model, provider latency or frozen provider expense. Those facts cannot be recovered by dividing an answer total or correlating timestamps.

Record future facts at the parent provider boundary. The child supplies only a bounded code-owned item label and requested field list. The parent supplies the existing logical call, exact provider/model route, measured start/end and latency, normalized cache/reasoning counts, and frozen provider-rate arithmetic. The call still has one charge even when it supplies multiple fields. These are diagnostic facts, never new spending authority.

Answer-v1 still holds provider calls in memory and saves them in the existing final correction batch. No per-call journal, marker, extra DB synchronization, model call, retry, rate limit, admission rule or debit is added. Its unknown-usage/crash path still retains the estimate and may lack call facts; this change does not make that path crash-durable. Generator/legacy calls attach facts to their existing response journal in the same completion transaction. An index supports exact attempt joins; ambiguous bindings remain unknown rather than multiplying rows.

Provider expense and user retail charge remain separate. Answer expense uses the parent catalog's frozen snapshot, measured cache buckets and existing conservative UTC call-interval rate rule. It is calculated expense, not provider invoice confirmation. Missing/invalid price evidence is unknown, never zero. Legacy expense remains the actual recorded attempt amount divided by its frozen scale. No export-time current-price lookup or historical backfill occurs.

Everything uses fixed field projections and the existing owned stable-id pagination. Numeric provider-native token fields and valid join IDs survive both scrubbers. JSON null remains explicit unknown; credential-shaped values and real keys/tokens stay redacted. Raw prompts, provider responses and arbitrary quote fields are not added to cost evidence.

## Migration and release

- Additive migration: `20261114051000_future_call_evidence.sql`. Adds nullable staging evidence, an immutable-completion guard and lookup index. Extends the existing receipt finish function only to accept a bounded optional `call_evidence` member.
- A source-comparison test proves the money function is otherwise unchanged: debit, rates, rounding, posting, idempotence and replay checks stay the same. Old records and old writers remain readable/valid. New runtime readiness refuses missing migration before dispatch.
- Migration inventory includes the constraint, index, guard function, attached trigger, exact finish-body marker and readiness sentinel. Real disposable-DB tests cover a complete ordered rollout, replay, missing/partial readiness, owner isolation and immutable evidence.
- Companion producer sources: interview-engine fdc99f5a2f3a845f0fc452a48dec90c6dfdeea8e and spec-generator d31345626771245867d1b08f7969329d85718b4c. Acceptance wheels match those source bytes.
- Exact child contract 0.4.0 wheel is identical in delivery, engine and generator (SHA256 `68b47c19c9ad84cf096977201e19ce910dfdda01d167dad8f18991e06dc5f056`). vmodel-contracts stays 0.29.0. No verification-loop pin changes are needed.
- Merge the companion set before a combined runtime rebuild; do not run a new producer against an old parent. Delivery-only rebuild is insufficient for the sibling-source full image.
- After the active Architecture run settles, the owner runs the normal `req rebuild --apply`. Its migration step must apply this file before the new runtime starts; do not choose Skip. The usual owner database confirmation is still required. No manual config editing, paid rerun, live DB mutation, merge or restart was performed for this work.

## Local proof

Delivery fast gate: both halves pass (2534 tests total, 4 skipped). Focused real-PG capture, quoted-cost arithmetic, replay, migration, tenant isolation, redaction, broker and readiness checks pass. Real signed Everything ZIP download verifies intact counts, grouped fields, logical-call/job/answer/contribution joins, latency, frozen expense and exact retail charge. A fresh-process real interview graph with fake HTTP covers success, missing usage, Stop and HTTP error while preserving start/finish-only accounting. Mandatory fresh-start acceptance runs all four real HTTP/startup/PG/child scenarios with fake vendor HTTP.

Engine: 797 fast tests pass, 3 skipped. Generator: 1134 pass; the pre-existing Documents fake lacking `recent_lifecycle_events` still fails, reproduced separately on clean current main. Hosted CI and a complete Docker build are not claimed. No paid provider was used.

Test expectation changes are explicit: inventory counts increase by one; the routing fixture supplies the newly required catalog readiness seam; exact contract distribution expectations move to 0.4.0; JSON null metrics now assert preserved unknown rather than false redaction. No validation or money assertion was removed.

Lesson: an export cannot repair facts that were never recorded. Capture exact identity and measurement at the call boundary, keep unknowns visible, and do not confuse diagnostic provider expense with a second debit.
