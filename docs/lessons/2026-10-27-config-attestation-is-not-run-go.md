# Config attestation should not require hash transcription

The item6 review receipt already binds the exact source bytes, run identity and
vendor-derived review hash. Copying that hash into a second command adds manual
work without replacing the actual review.

The normal terminal review now displays the same nonsecret values and conditions,
then asks for exact `yes`. A secret-only source change or identity/review change
while the prompt is open refuses the attestation. The saved receipt retains the
same format and compile verification, and launch_authorized remains false.

A pipe is not a review prompt: noninteractive review-config stays read-only even
when stdin contains yes. Empty/no/EOF do not write a receipt. The existing exact
--confirm hash route remains for compatibility, but is not the normal instructions.
No --yes shortcut, compile, Docker call, real run or money approval is added.
Other gates, launch go, sandbox evidence and authentic webhook requirements stay.

New attestation tests failed first before implementation. Terminal integration
uses a real pseudo-terminal, sees the review before answering and verifies only
the receipt appears. Noninteractive CLI remains unreviewed. Config/scaffold/
vendor/diagnostics tests pass without provider calls. Hosted CI and full-repo
suite unverified. No live DB, deployment or paid run performed.
