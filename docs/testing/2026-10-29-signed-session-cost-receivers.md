# Sign the three PG-backed session-cost receivers

Preparation for decision7,not the default bootstrap flip. The proposed PG-first default installs real tenant middleware at app import;three existing cost receivers then fail401 because they make unsigned requests despite owning scoped PG data. The bootstrap remains held on a local branch,not included here.

Use the existing signed pg_task_app and owner headers for these three receivers. Explicit local compatibility mode remains after fixture construction because the approved response contract is local usage evidence,not cloud execution/cost policy. PG store,real signature verification,session liveness,tenant context and duplicate-binding rejection remain. No middleware removed;no assertion changed or weakened. Existing exact2.5credit/evidence response,unknown-session404,immutable duplicate rejection and original-cost/binding preservation assertions are unchanged.

Measured on Python3.11.17:
- Held future bootstrap before repair:all three cost receivers failed unsignedHTTP401 in the recorded40failed118passed slice25.47s.
- Current default,three receivers plus scope guard:4passed1.13s.
- Held future bootstrap after repair:4passed0.72s. Restored current conftest/probe afterward;no bootstrap change committed.
- Expanded tasks/cloud API/bootstrap:139passed20.41s.
- No production change. Full suite/hostedCI unverified. Other mixed unsigned/local-runner receivers remain migration work;not hidden by this PR.

Lesson:being PG-backed does not make an HTTP receiver signed. Add the actual owner token and preserve the existing compatibility contract rather than bypass middleware or reinterpret its assertions.
