# Prove typed refusal,not ambient engine outage (2026-10-29)

Original built-runtime503/no-subprocess test relied on global SQLite and whatever
engine connectivity existed. Port to isolated poisoned PG fixture;keep local
route semantics explicit. Fake typed default-route failure,assert called once,
503,no runner call,and task not_started. No real network/model call and no
production change. Fixture guard red1failed0.87s;final41passed0skips4.97s with
cloud API/bootstrap. Hosted CI/full suite unrun. Not signed-cloud execution.
