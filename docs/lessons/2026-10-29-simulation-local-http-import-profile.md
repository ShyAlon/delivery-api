# Simulation HTTP validation needs its local import profile

2026-10-29: five simulation upload/start/error HTTP tests select local config
after app import; held PG-first routes fence replay as intended for cloud before
reaching validation. Fail-first5failed/5passed0.75s.

Move only these five HTTP cases to existing poisoned restrictedPG local import
process, attach Simulation to that process's actual restrictedPG store. Keep all
original upload201/simulated, bad filename/malformed JSON/unknown stage400 with
guidance, actual ASGI-loop replay start200 and structured start-failure500
assertions. Existing title-inference/local-canonical/direct-validation cases
remain in their scopedPG workspace; fixture is explicit rather than autouse.
Retain restricted-role/SQLite poison/simulation.store identity proof. All10cases
kept,no assertion/coverage loss,production/default-bootstrap untouched. Cloud
replay fences stay exercised in cloudAPI,not bypassed to make tests pass.

Simulation validation + cloudAPI Python3.11.17:46passed current8.02s,
46passed heldPG-first8.18s,one platformdirs warning each. HostedCI/full suite
unrun. No deploy,liveDB,provider calls or spend. Bootstrap restored/unpublished.
