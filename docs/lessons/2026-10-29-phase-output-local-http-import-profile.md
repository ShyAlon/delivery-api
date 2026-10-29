# File-backed phase output needs the local import profile

2026-10-29: five file-backed phase-output tests switch config to local after
app import. Held PG-first middleware responds401 before projection, resulting
in missing status keys. Fail-first5failed/1passed0.85s.

Run these five local HTTP cases in existing poisoned restrictedPG local-compat
subprocess import profile. Preserve all previous-phase labeling/current partial,
generator-key projection, empty-partial fallback, redo freshness and invalid
artifact assertions. Session bindings are unique per case; task/session/file
content identities still agree. Retain the sixth real restricted role/SQLite
poison fixture test explicitly. All6cases kept; no coverage/assertion loss.
No production, default-bootstrap or output UI design change. These file-backed
local projections are not a replacement for cloud artifact authorization;
cloudAPI owner fences are exercised alongside them.

Phase-output + cloudAPI Python3.11.17:42passed current8.18s,
42passed heldPG-first8.29s,1platformdirs warning each. HostedCI/full suite unrun.
No deploy,liveDB,provider calls or spend. Bootstrap restored/unpublished.
