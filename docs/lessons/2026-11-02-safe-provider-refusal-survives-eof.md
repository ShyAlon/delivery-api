# Preserve the known parent failure after child EOF

The parent observed HTTP refusal (for example 415), recorded a safe reason, then closed the broker channel. The child lifecycle subsequently ended with descriptor EOF, masking that known reason.

Only ParentProviderHTTP's typed sanitized refusal is retained by the parent channel. After channel cleanup, it replaces the secondary EOF failure; it keeps the reconciliation warning and never replays. Unknown exceptions and raw vendor bodies remain private. Failure to prove channel quiescence is never replaced. No child protocol or wheel change is needed.

A fail-first real socket-channel test saw only descriptor EOF before repair. Three refusal cases now preserve HTTP status and reviewed error codes, exclude private vendor text, mark the attempt uncertain and prove one HTTP dispatch.
