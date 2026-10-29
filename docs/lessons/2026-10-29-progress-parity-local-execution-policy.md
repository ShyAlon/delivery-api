# Progress parity tests the local subprocess adapter

2026-10-29: held PG-first changes policy to cloud. Existing parity case uses
arbitrary per-phase output paths and no admitted worker gateway; cloud capture
correctly refuses it. Included in fail-first3failed/4passed1deselected1.06s,
parity itself fails current-generation session binding before progress checks.

Make that local adapter premise explicit and bind its real PG task to its unique
engine session before emitting progress. A binding alone correctly advances to
cloud output-path denial; do not bypass cloud namespace/gateway validation.
Keep the one cumulative all-phases case, original dedupe/gate/revision/source/
control/completion assertions, real subprocesses and restrictedPG. No phase
split, source assertion weakened or coverage lost; no production/bootstrap edit.
Cloud owner/execution fences remain tested alongside it.

Parity + cloudAPI Python3.11.17:37passed current4.01s,
37passed heldPG-first4.18s,one platformdirs warning each. HostedCI/full suite
unrun. Separate progress-protocol integration still fails missing interview_engine
and is not changed by this PR; its local adapter patch is held separately.
No deploy,liveDB,provider calls or spend. Bootstrap restored/unpublished.
