# Runner lifecycle ports must keep framed failure causes

A storage port must not blur an incomplete stdout read into a different module
name or replace a producer contract error with a generic failure. Keep split-read
framing,phase labeling,heartbeat/stall and release-before-handoff assertions on
the real scoped store. Synthetic children test lifecycle without paid providers.
