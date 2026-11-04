# Exact image identity and invalidation

CI unavailable, local gate only. WIP until applicable tests and owner Docker-host gate.

The image config SHA256 is not its registry manifest SHA256. Bind both after
local acceptance, publish only into a verified private package, and deploy only
the manifest whose config is the tested local image. One shared runtime planner
owns fixed options; DSNs and endpoints are values, never mode selectors.

A deleted receipt file is insufficient invalidation: stale in-memory/on-disk
acceptance can still authorize preview. Revoke the stage and all descendants
before attempting a replacement build or acceptance. A failing regression test
must exercise restart from the saved state, not just the current object.

A fake command gate cannot prove container networking or lifecycle. The actual
fixture process test confirms schema/pin setup only; the unchanged amd64 service
must still complete signed Start/answer/active-call Stop and retained PG evidence
on the owner's Docker host before an image may be accepted.

Never transplant old shell-source assertions without preserving their product
and safety duty. Examples: visible-before-input PTY confirmation, real private
Back office ownership, retained browser failure diagnostics, required settings
before current-service stop, and health separate from signed route admission.
