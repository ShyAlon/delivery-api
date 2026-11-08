# Comparing a thing to itself verifies nothing

step_pull_all "verified" the six pulls by comparing origin/main refs to
origin/main refs - the same function on both sides of ==, so the check was
green even if every checkout sat a week behind. The delivery-api checkout had
a real HEAD==origin/main gate in preflight; the other five had theater.

Verification has to cross an independent boundary: a remote ref against a
local HEAD, a claimed state against an observed one. Same-source comparisons
are not checks, and they are worse than no check because they teach everyone
to trust the green light.

When one repo in a set has a real gate and the rest have a vacuous one,
assume the gap will be discovered by a stalled machine at the worst moment,
and close it with the per-repo comparison that names the laggard.
