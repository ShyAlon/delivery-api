# Keep the real child Start journey blocked while asserting progress

A green component journal test did not prove the child runtime selected that
journal. Hold the fixture provider's first response during actual
ChildInterviewClient.start -> fresh child -> real graph -> migrated PG. Read the
independent progress page before releasing the response. Empty pages fail the
test. The original buffered producer failed this assertion; the observational
producer passes. Pin its wheel and checksum for installed acceptance too.

This is a progress-only producer change, not the provider envelope fix. The
fixture in this test deliberately retains its original HTTP behavior so the
progress regression can be isolated. Strict HTTP fixtures live in the separate
shared provider-envelope theme. No live database/provider or paid run.
