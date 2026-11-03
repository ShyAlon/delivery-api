# Page component must own shell and lifecycle

Sharing teardown helpers did not make Page one component. Customer pages still built heading shells, both documents owned main landmarks, and shell geometry lived in global stylesheets. Seven explicit construction-debt entries recorded that gap.

Page now owns persistent customer shell construction, private content-frame adoption, heading wrappers, header/main/footer geometry, and the single route lifecycle. Compatibility entry points delegate to that class. Pages still supply body content and action behavior. CSS is inserted after its original stylesheet anchor so the former cross-file cascade order stays stable; style installation is idempotent. Private content and labels remain supplied by the private app, never embedded in shared customer assets.

Moving main construction exposed an import-time contract: several page modules capture app before boot. A small Page boot entry creates the owned main before importing those modules. Moving lifecycle into a shared class must not pull the customer's framework import map into the private app, so the customer shell entry supplies its renderer explicitly. These were caught by real browser checks, not papered over with fallback grants or altered lifecycle assertions.

All seven recorded Page construction entries are gone. The separate zero-exception gate theme still needs to strengthen static detection of programmatic construction and Page style bypasses; an empty legacy inventory alone is not proof of all possible bypasses. Existing Table/Dialog boundaries stay enforced.

Validation includes persistent shell identity, auth/font geometry, stale mounts, polling cleanup, decision pages, access retry, confirmed Delete, and private jobs. New tests prove one set of landmarks, shared compatibility lifecycle/abort, one-time style installation and desktop/mobile task layouts. No production data writes, provider run, deploy or merge.
