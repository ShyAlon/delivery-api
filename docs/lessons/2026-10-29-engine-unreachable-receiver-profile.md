# Engine-unreachable receivers need the right scope and profile

2026-10-29: four engine-down tests cannot reach their intended contract under
held PG-first. MockTransport unit call lacks tenant scope; three local HTTP
checks toggle local config after tenant middleware installation. Fail-first:
4failed0.58s. Do not turn off middleware or fake cloud execution admission.

Give unit transport-failure case the existing scopedPG workspace. Move three
local HTTP cases to existing poisoned restrictedPG local import process. Unique
task/session names avoid fixed-owner collisions. Remove obsolete config-toggle
fixture only. Keep all original EngineUnreachable/baseURL, actionable503/error/
cause/next, no-events-before-preview, and process_live/waiting false assertions.
All4cases retained, no assertion/coverage loss, no production/bootstrap change.
Explicit DownEngine/MockTransport doubles remain; this is not a real engine run.

Engine-unreachable + cloudAPI Python3.11.17:40passed current6.20s,
40passed heldPG-first6.27s,one platformdirs warning each. HostedCI/full suite
unrun. No deploy,liveDB,provider calls or spend. Bootstrap restored/unpublished.
