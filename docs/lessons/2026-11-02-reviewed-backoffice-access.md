# A page is not a capability grant

Model-pin review failed on its first use: it requires services.operate in both
protected config and DB registry. Read setup copied only five read permissions,
and later migration setup could silently remove a previously reviewed service
permission. Widening the SQL allowlist did not enroll an operator.

Use one fixed action/purpose inventory for owner review and private-page checks.
Enrollment starts dry, requires an existing matching sole owner, prints no
private values, and asks for an exact typed confirmation before updating that
row and the protected file. Preserve old reviewed capabilities, never enable a
new identity, never copy unapproved DB effects into local config. Recheck registry
and file drift after confirmation. A DB commit/file save split is reported as
booleans, not called atomic or silently retried.

Missing private-page capabilities disable controls and name the gap. This is a
presentation boundary, never a replacement for the endpoint/RLS checks. Receipt
reads are not purchases, expense-ceiling editing is not paid activation, stored
no-byte proof is not an uncertain-bill write-off. Do not add unrelated grants.
