# Progress migration preserves state authority (2026-10-28)

Moving nine progress browser functions (13 cases) to signed PG retains the
existing waiting-state precedence,collapsed/expanded history persistence and
heartbeat-wrap checks. Mock task/SSE payloads still define presentation inputs,
so this is not proof of real engine liveness. Keep the original assertion scope.

The signature guard failed first. A scripted edit briefly spilled into the
neighboring route decorator,causing collection failure;reverted that unrelated
change before running. Final13cases plus guard,cloud API and bootstrap passed52,
skipped0 in36.39s. Phone heartbeat and desktop expanded progress pixels are
readable. Production unchanged;hosted CI and full-suite remain unverified.
