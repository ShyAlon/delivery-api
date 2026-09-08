# delivery-api

Orchestration and nothing else: run links, the run state machine, background
jobs, storage, budget enforcement, and the per-stage cost rollup.

## Decisions baked in

- **Links, not accounts** (scoping doc Decision 5): one personal token per
  tester gates the run, ties interview/spec/verification together, and gives
  per-tester rate limiting. Link creation is admin-only.
- **Background jobs from line one** (ADR-0006): Vercel Hobby caps functions
  at 300s and generation is a multi-minute pipeline. Nothing here is
  request/response that will become a job later.
- **The budget hard stop lives here**: modules report every LLM call to
  `/runs/{id}/usage`; a call that crosses the per-run cap stops the run
  (`budget_stopped`). Per-run cap default $6, global cap default $100
  (scoping doc: ten testers worst case). The versioned price table lives
  with the modules' telemetry; this service trusts only its own ledger.
- **Zero-cost posture**: sqlite in the skeleton; the `Store` interface is
  the seam where Supabase Free slots in. No paid infra without asking.

## API

```
POST /links                  (admin) mint a tester link
POST /runs                   start the run behind a link token
GET  /runs/{id}              run state machine status
POST /runs/{id}/usage        module cost reports (hard stop enforced)
GET  /runs/{id}/artifacts    produced artifact refs
GET  /health                 liveness + global spend
```

## Boundary rule

Talks to interview-engine, spec-generator, and verification-loop only through
vmodel-contracts payloads. The skeleton can wire them in-process behind
explicit flags for an all-in-one demo box; production wiring is service
calls. Never shared code beyond the contracts package.
