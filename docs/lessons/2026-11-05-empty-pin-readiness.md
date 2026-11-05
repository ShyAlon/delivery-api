# An empty reviewed pin store is setup, not a provider outage

The owner reached the new local PostgreSQL app on8002 and tried Start before
approving any local model pins. Empty pins correctly fail child runtime install.
The old customer selector still marked Ollama available and called DeepSeek
"temporarily unavailable". Start then showed an obsolete scoped-protocol fence.
That hid the actionable prerequisite and suggested an unverified provider outage.

Use a typed empty-pin startup error, retained independently of private exception
text. While runtime is absent, neither route nor Start is available. The fixed
operator message names Back office > Model pins; other users are asked to contact
the operator. Arbitrary provider text/URLs never become repair instructions.
Do not claim missing pins for other install errors. Pin approval remains the
explicit repair event; do not invent pins, select substitutes or send model calls.

The selected provider config still matters: REQUI_PIN_PROVIDERS defaults to
deepseek. An Ollama pin alone does not activate Ollama unless explicitly selected.
An empty DB test must not be mistaken for a ready intelligent-model full run.
