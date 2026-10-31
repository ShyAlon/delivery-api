# Constructor wiring needs an explicit operator boundary

The trusted runtime installer worked only when a caller supplied its object. The normal launcher never did, and inherited child flags could ambiguously select old behavior.

Add a separately opted-in attended-paid preview selector; require loopback preview boundaries before constructing the exact reviewed typed runtime. Strip inherited selectors in preview-exec and refuse paid startup without a separate owner assertion. Defaults and deployed cloud remain closed. No launcher was run.

Tests cover selector/origin/profile refusal, local-only behavior, real installer construction without dispatch and existing pool/launcher/runtime tests. No test was retired; no cap, credit, admission row or live setting changed.
