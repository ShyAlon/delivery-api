# HTTP fixtures need distinct globally owned engine sessions

Per-test tenant identity does not make a hardcoded engine session unique. PG
correctly refuses reusing another owner's session. Generate a unique session in
each ported fixture and align the fake engine snapshot with it. Preserve the
original typed input,rationale,error and event-dedupe assertions. Never weaken
session ownership or remove the uniqueness guard to simplify tests.
