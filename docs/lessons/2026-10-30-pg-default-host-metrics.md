# PG defaults need cross-host reads and abandoned-source retention

The PG application previously needed an opt-in to avoid the SQLite resource
sampler. Selecting PG was not enough: the operator read path saw only the
current writer source, and a retired source had nobody left to roll up its raw
rows. A shared source would hide that retention gap by mixing unrelated host
CPU and memory series.

## Boundary

PG application configuration now chooses PG metrics by default and refuses a
SQLite override. The legacy SQLite reference profile remains explicit and is
not a failure fallback. There is no history import or old-path removal here.
The default source is `host:` plus the hostname. Containers with unique hostnames
remain distinct across replacement; their raw data is still maintained after
retirement. Deployments that reuse a hostname for concurrent samplers must set
`DELIVERY_METRICS_SOURCE` to distinct per-host/container identities. Never share
one override across replicas. A blank override is an error.

Writer transactions have bounded checkout, lock and statement waits. Resource
samples reject invalid identities, negative/non-finite values and timestamps
outside the supported calendar range; SQL checks cover direct raw numeric writes.
Failures show a safe class-only diagnostic and never create a local DB.

Operator reads use the existing `logs.read` registry, source-labelled series,
bounded pages, twelve-point timelines and a repeatable snapshot. Capacity still
reports local slots/process memory separately from cross-host resource samples.
Failed metrics reads return a safe 503; authorization failures retain their own
status. Per-host CPU current/average/maximum and memory values remain visible.

## Maintenance

A fixed SECURITY DEFINER entrypoint touches only the four metrics tables, takes
no source/SQL/table parameters and returns counts only. Migration ownership is
trusted database-admin authority, not application authority. Its owner must
remain the trusted migration owner able to see those fixed tables under RLS.
Only `requi_metrics_writer` receives EXECUTE. PUBLIC and Supabase's independent
default role grants are explicitly revoked. No extra SET role membership is
needed and the writer gains no cross-source SELECT or customer/credit access.

Hourly maintenance handles at most 32 source batches and 100 old runs/days per
source. Source order rotates hourly to avoid a permanent first-page bias. Writer
and maintenance use the same advisory lock; maintenance skips busy sources.
Runs/days must be entirely older than the configured cutoff, which must also be
at least one hour behind wall-clock time. Summary and raw deletion are atomic.
Existing conflicting summaries or ambiguous identities preserve raw evidence
for explicit reconciliation. This is bounded eventual cleanup, not an exact
instant deletion deadline. Very large backlogs need repeated passes; a timeout
rolls back that pass. Permanent summary rows are intentionally retained.

Startup validates source selection, writer table access and function EXECUTE
without invoking maintenance or changing production data. Applying migrations
and granting the app login SET membership remain operator rollout steps.

## Verification and changed tests

Fail-first tests cover PG selection/no file creation, unavailable safe errors,
invalid samples, direct SQL NaN, actual lock timeout, retired-source cleanup,
maintenance ACL defaults and distinct cross-host series. A real registry read
asserts repeatable-read isolation while another host commits a new sample; the
next read sees it. Late samples cannot overwrite rolled summaries. Hot and
ambiguous runs remain raw.

The explicit-blank-source case replaces the old expectation that an absent
source is invalid. Absence now has a tested host default; explicit blank still
fails. Startup's exact successful check count changes from ten to eleven,
with a new failing-resource-store crash-matrix case. Capacity HTTP fixtures now
seed PG samples and the real operator registry, instead of a local SQLite file;
401/403, numeric content and capacity assertions are retained. No assertion was
relaxed to make a failing path green. The new browser test uses real PG host
samples and an installed registry reader in the existing Debug dialog.

No live DB writes, deploy, task reset, or payment is part of this work. Hosted
CI is unverified until checked separately.
