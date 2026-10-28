# Log write loss must not disappear into logging

Activity-file initialization and stage-file errors were suppressed; the engine
mirror disabled itself silently. Logging handlers also catch their own write
errors, so catching around emit alone misses failures. Override handleError for
rotating evidence handlers. Record bounded process-local degradation and send one
stderr warning per source, without exception text, paths or payloads and without
calling logging recursively. Primary work remains nonfatal. Operator-only Debug
exposes the retained failure state; absence of recorded failure is not a promise
of durable delivery. Restart resets this state; the UI says so.

Failing-first tests cover activity/stage/mirror errors, once-only safe warnings,
nonfatal behavior and endpoint audience. Desktop Debug notice inspected.
