# Current non-actionable gates are not historical

A current idle gate returned `actionable=false`. The browser treated that as
proof that the review was an older revision, labeling it "Historical gate
review" and explaining its blocker as a past review. Actionability answers
whether a decision can be made, not whether a revision is current.

Expose the revision comparison as `historical` in the review response and use
it only for the banner/blocker wording. Keep the actionability expression and
all action gating unchanged. Current idle gates say they are not waiting for a
decision; older retained revisions keep the read-only historical notice.

## Evidence

- New real signed Postgres browser cases cover current idle, current waiting,
  and retained historical reviews. They inspect both API flags and notices.
- Before the fix, the idle case failed on its visible current-gate banner:
  1 failed in 8.70s. No timeout was relaxed.
- After the fix, new cases plus the cloud API module: 39 passed in 13.55s.
- Relevant gate browser/API slice: 8 passed in 20.63s on current bootstrap;
  8 passed in 21.50s with the held Postgres-first bootstrap.
- Inspected screenshots for all three cases. Notice text is readable and the
  layout is intact. Empty artifact warnings remain: these fixtures deliberately
  do not seed artifacts, and are not evidence of completed phase results.

## Assertion and scope accounting

Three new cases; no existing assertion changed or removed. No coverage loss.
No production deletion. Only an additive response flag and two UI wording
branches change. This fix does not explain or bypass the separate cumulative
all-generator navigation timeout. Hosted CI and the full suite are unverified.
