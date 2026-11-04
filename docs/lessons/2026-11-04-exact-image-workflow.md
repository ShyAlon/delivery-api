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

## Failed startup must leave evidence before cleanup

The first actual Mac image built but failed health. A generic timeout and finally
cleanup destroyed the only container logs. Poll container state as well as HTTP,
stop waiting after exit/unhealthy, and capture bounded owned state and log tails
before cleanup, including stderr and the last health exception. Keep the file
outside temporary scratch; never dump environment or fixture credentials.
A native fixture that only seeds PG is insufficient startup coverage: also run
the exact entry/preflight and prove HTTP health. This still is not a Docker gate.

## Requested port binding is not actual publication

An internal-only Docker network can accept -p while providing no host mapping.
A healthy app plus refused host connection must be diagnosed as a host path,
not waited out as app startup. Keep app outbound isolation with a separate,
fixed-destination ingress relay; verify Docker's actual mapping. Derive probe
origin and mapping from one planner. Bound elapsed time and every blocking call,
not merely the number of retries. Record build cache evidence before claiming
why a build took longer. Whole-source COPY before dependency install defeats
otherwise valid caches when only host tooling changed.
