# Read cycle findings from three diagnostic ZIPs

Embedded delivery builds: 2c5e5064, 4dd91ec2, 01ee6ff8. Providers/models differ
(Ollama qwen failed; Ollama phi4; DeepSeek flash), so wall-time attribution is
not controlled. The comparison workbook contains n, means and sample variance.

Latest stable window (12:34:20-12:38:35 UTC):
- credits/cost/cost-phases repeat at about 30 seconds, averaging 12 statements
  per cycle. The new browser event refresh removes that idle trio before the
  60-second safety cadence; no claim of zero lifetime reads.
- task GET averages 4 statements / 1,363ms, versus 8.2 / 2,804ms in the prior
  stable window. Live engine/session/phase work means the cold snapshot count
  of 2 does not describe every task request.
- phase-output remains 7-8 statements / 2,263ms average. It performs task and
  session authority, authorized namespace and artifact projection checks.
  Duplicate read elimination may reduce this, but ownership checks must remain.
- POST Start acceptance is 16 statements / 4,689ms (DB 3,241ms, auth 902ms),
  versus prior 14 / 3,756ms. No measured Start improvement. It binds authority,
  reserves the initial start and writes initial durable events. Not removed by
  a UI timer change; do not call the 202 response first-question latency.
- client-log authentication was fast on most cached requests but some misses
  reached 1.8 seconds. The shared session-check cache bounds revocation at 30s.
  Do not bypass authentication or lengthen that bound to improve this metric.

Progress source timestamps were retained 26-56 seconds later; engine first
model response preceded the existing Start response log by about 31.8 seconds.
The old log was after final progress drain. New diagnostic boundaries distinguish
actual engine return, final drain, per-page fetch, ingestion and source age.
No speculative fix to engine execution ordering is included.

No complete intent/ConOps/downstream phase durations are available in these
captures. New post-merge interview is a separate comparison run.
