# Cloud classifications need explicit owner policy (2026-10-28)

Shy's Q2 answer'differed' changes cloud disclosure policy. Keep task ownership
lookup and authorized session paths before reading shadow classifications.
Real signed API test proves owner differed=true,other tenant denied,unauthed401.
Debug browser proves rendered differences;normal page remains chip-free and raw
execution stays excluded. Update the old classification-excluded assertion
explicitly,not as a silent fixture port.

API red1 in0.70s,browser red1 in33.27s;final42passed0skips11.36s;visual rerun
1passed3.33s. differed=true pixels inspected. No deploy;hosted CI/full suite
unverified. Other accepted retirement/redaction decisions are separate themes.
