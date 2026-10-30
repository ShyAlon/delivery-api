# Malformed credential input must never become a diagnostic value

The component-difference diagnostic trusted libpq's parsed host. A password with
an unescaped @ was partly reclassified as host, so printing that supposedly
non-secret component exposed a password fragment. This is a credential-handling
bug. Never print or repeat the observed fragment.

Both admin and app URLs now validate raw authority before parsing, connecting,
identity lookup or target comparison. Exactly one raw @ is required; user/password
accept only unreserved bytes or complete percent escapes. Raw / ? # delimiters,
invalid percent escapes and identity/endpoint override query parameters refuse
with a fixed, fragment-free error. Valid percent-encoded password bytes are not
rewritten. The tool does not auto-split/rewrite ambiguous credentials on the last
@ because that could silently change the destination or password.

Owner steps: edit the saved admin URL locally, encode reserved characters in its
password only (@ becomes%40; /%2F; ?%3F; #%23; literal% becomes%25). Do not encode
the whole URL, double-encode existing escapes, paste the credential or mismatch
output in chat. Consider rotating the database password because a fragment was
exposed, then update saved credentials. Keep app URL unchanged unless its own
validation reports malformed userinfo. Re-run the owner dry command after merge.

Six cases failed first;19 focused cases pass including raw-@ no-fragment
regression and encoded-password preservation, identity and disposable rollout.
No live DB read/write, owner-machine command, credential-value read, deploy,
spend, Docker, hosted CI or full suite was run.
