# 2026-10-27: Bind the provider before the worker starts metering

Displaying two route pills was not supporting two providers. The old factory
had one usage adapter, retail formula, context bound and vendor verifier. Simply
allowing a two-row catalog could settle local usage with paid rates or omit paid
compute. Compute begins before the first model request, so late routing is not
safe either.

Keep independently validated single-provider children. Bind one child from the
durable worker provider:model choice before invocation. Reserve enforces the
same choice. Start/settlement checks the immutable attempt quote before raw
usage is recorded or normalized. A conflicting quote is held for reconciliation,
never interpreted using the current route or guessed from usage fields.

Interview jobs previously carried empty execution options. Pin the effective
program model map, the initial request for construction, or the explicit target
while constructing a route-switch candidate. Reset target context on every exit.
Do not bill a new candidate using the old program's route.

Explicit both preview mode combines independently pinned configs, with local
model loading disclosed before confirmation. Preserve DeepSeek-only mode's lack
of Ollama dependency and local-only mode's absence of a provider key. No silent
fallback, copied credential, or inferred price.

Acceptance includes alternating local/paid calls over real admitted transports
against disposable Postgres: exact quote/usage, local zero charges and no vendor
or balance contact, paid source/balance checks, plus real worker compute segments
only on paid jobs. Hermetic tests are evidence about routing and money mechanics,
not permission to make a real paid call.
