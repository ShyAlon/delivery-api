# One startup connection timeout needs one diagnosis

The first preview readiness failure printed ExitCode0 even though Docker was
still running its preflight. Empty main logs were collected before the final
report existed. A later exit2 proved a refusal; the completed report identified
a DB connection timeout, not a model-picker defect. Do not infer a clean exit,
a blocked university network, or a schema defect from elapsed time alone.

Emit startup and per-check markers before blocking work, flush the refusal, and
keep bounded evidence outside the preview tmpfs. Automatically collect state,
health history, logs and the last stage. A running container's default ExitCode
is not an exit. Missing evidence means cause unconfirmed.

A driver connect_timeout may apply per resolved address. Bound the whole startup
connection budget, and do not repeat DB connects after the first failure. Name
dependent skips without turning them into independent missing-schema findings.
Keep host TCP reachability explicitly host-scoped, preserve multi-host/socket
semantics, redact values before serializing evidence, and do not reuse an old
refusal after the container restarts.
