# A local dispatch callback does not install a cloud producer

The durable worker seam uses a fake local orchestrator callback while keeping
real PG task/session/job rows. Under held PG-first it tried cloud child dispatch
and correctly refused the missing installed gateway before calling that callback.
Fail-first full module with actual dependencies: 1 failed / 64 passed in 90.31s.

Select explicit local execution policy in only this seam test. Keep the real
PG adapter, unique session binding and original done-job, exact callback and
mismatched-resume denial assertions. No assertion changes, coverage loss, skip,
production edit or bootstrap flip. This does not claim cloud execution coverage;
that is tested by the signed/cloud modules separately.

Actual interview-engine source f829cfb673d9c7ec49e4697708cf063a908e26fe and pinned
Playwright browser resolve earlier environment-only failures. Full module held
PG-first after fix: 65 passed in 89.45s. Hosted CI/full suite unverified.
Final current module after fix: 65 passed in 89.50s.
