# Verify what the producer writes, not what the consumer wishes it wrote

req rebuild's accept-local step was unpassable end to end: its verifier
required accepted-image.json, a file only the --publish path writes, while
local acceptance actively deletes any older copy. The merged unit tests
crafted run dirs with receipts by hand, so every test was green and the
first real owner run failed deterministically.

The verifier now derives local acceptance from the run's own stage evidence:
the run directory name and state.json input_key are recomputed from the four
source revisions, platform, and both contracts, then the build and
local-acceptance stage evidence must agree on an image that still exists.
The publish receipt keeps its stricter publication meaning untouched.

Two rules: an end-to-end consumer/producer pair needs at least one test that
uses the producer's real output shape, and a verification that can never
pass is worse than no verification - it reports a lie about what failed.
