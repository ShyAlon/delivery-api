# A process envelope is data, not execution authority

The child-process move first needs an inert contract and honest baseline.
Do not turn a well-formed JSON message into a capability: every binding must
be compared with verified durable state, and the eventual receiver must check
live auth, lease, generation, stop and installed-anchor evidence independently.

Tests failed first with the module absent. A later-generation missing-anchor
regression failed before its validator was added. Strict frame validation
rejects duplicate keys, coercion, unknown versions/types and command/path
fields. A wire fixture pins the proposed cross-repo shape; a static test pins
that no runtime consumes it in this phase. Existing retail subprocess refusal
is tested directly, not assumed from a comment.

No process has been launched and no paid execution has been enabled. Actual
crash/recovery and idempotency require durable consumers in later phases;
the baseline lists those acceptance cases without claiming they already pass.
