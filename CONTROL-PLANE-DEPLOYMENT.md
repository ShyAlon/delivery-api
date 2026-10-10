# Item4 delivery-only container evidence

No infrastructure was deployed, no credentials/account state were accessed and
no external engine/provider/payment call was made. This is a fenced control-plane
profile, not a full working service launch.

Changes:
- Build context whitelist excludes environment files, repo history and local data.
- Docker builds delivery wheel plus hash-checked bundled contracts, no private Git
  token, engine/generator package, Ollama or provider SDK. Runtime uses UID10001.
- Launcher forces delivery-only profile; startup does not recover local jobs.
  HTTP mutations, engine requests, queue/runner/private worker boundaries fail
  before effects. Health labels profile/execution/payment status, not readiness.
- Script requires immutable digest and private reviewed cloud env file. Candidate
  not published; old service remains through candidate health. Loopback cutover
  retains exact original container/config, traps failure and restores it. Earlier
  retained candidate/rollback container blocks a new deploy; no image/data prune.
- Workflow clean wheel gate uses isolated environment; manual image path performs
  network-none/read-only container health before publication, no mutable main tag.
  Remote deploy still needs explicit CONTROL_PLANE_DEPLOY_ENABLED and existing
  reviewed env file; neither is provisioned or enabled here.

Evidence:
- 168 focused tests passed before final workflow contract addition.
- Existing core PostgreSQL/producer complement68 passed,16 deeper retained graph
  variants excluded from bounded check, not claimed clean-run evidence.
- Clean delivery-only wheel installed in a new isolated environment, engine and
  provider packages absent. Real local HTTP health/UI/mutation-deny smoke passed.
- Stub Docker tests verify candidate failure preserves original, failed cutover
  restores exact config/container, rename failure cannot remove original,
  success remains loopback-only, immutable input and cloud env requirements.
- Docker/Podman absent here: actual image build/run and target amd64/arm64 remain
  unverified. Workflow gate is prepared, not reported as run. Image/base/dependency
  downloads are real build traffic, not provider calls or money tests.

Remaining rollout checks:
Actual container build/run on target architectures; approved cloud auth/schema
and restricted role state; credential provisioning; protected host config and
registry access; TLS/origin/read exposure; verified backup/restore; full chain and
step6 owner-scoped launch. Customer checkout UI remains separate mock review.
