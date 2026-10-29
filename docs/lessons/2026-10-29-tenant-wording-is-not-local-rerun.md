# Keep wording evidence separate from local rerun policy (2026-10-29)

A session-unavailable wording test bundled a local-only Rerun button expectation.
Owner Q4 approved tenant wording with no button. Signed PG port keeps every
known-state/warning/banned-phrase assertion,changes button count1 to0 explicitly,
and adds tenant rerun unavailable evidence. No production change.

Fail-first fixture guard1failed0.75s;old button assertion1failed3.71s on signed
PG;final45passed0skips14.46s. Full screenshot shows warning/history/no rerun;
Phase results still Loading is not claimed as verified content. Hosted CI/full
suite unrun. Local rerun contracts remain in their separate local suite.
