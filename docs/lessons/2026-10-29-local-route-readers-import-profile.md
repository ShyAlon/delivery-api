# Local model-route readers must import the local profile

Four existing route-preview/switch/sticky-default cases depend on local route
catalog semantics and local HTTP compatibility. Held PG-first installs cloud
policy before their case patches. Fail-first full diagnostic was 23 failed /
29 passed in 8.50s, including these four.

Run unchanged case bodies in the existing PG-local import subprocess fixture.
Keep restricted PG, poison SQLite, and preserve exact route availability,
conflict reason, input identity, one route event and sticky model assertions.
No case-identity changes, assertion changes, skips or coverage loss. This is
local compatibility, not signed cloud execution. Cloud API remains selected.

Four cases + cloud API: 40 passed in 7.06s current; 40 passed in 7.25s held
PG-first. Whole HTTP-cutover module still has other receivers outstanding.
No production edits/bootstrap flip/deploy/provider spend. Dated evidence here;
hosted CI/full suite unverified.
