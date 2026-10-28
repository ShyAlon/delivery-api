# Observe owned background work

Detached asyncio tasks can fail after an HTTP response is already sent. A done
callback should retrieve and safely record the original exception, keeping task
references until completion and preserving cancellation/await semantics. Thread
context must be copied at creation, not read from the new thread's empty scope.

Instrument our entrypoints rather than replacing process-global exception hooks.
Channels that catch and translate exceptions need evidence before that translation.
This does not turn SIGKILL, OOM or machine loss into in-process evidence.
