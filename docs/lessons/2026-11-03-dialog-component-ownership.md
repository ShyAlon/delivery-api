# Dialog content is not dialog construction

One Dialog class owns native and overlay construction, shared lifecycle, focus and backdrop behavior, and dialog-local CSS. Page files provide body content and options. Named wrappers remain only as small compatibility delegates, not a second implementation. The construction/style ratchet has zero Table/Dialog exceptions; seven Page entries remain.

A migration regression caught outside clicks landing on the shared shell rather than its host. The class now owns the backdrop hit boundary; callers provide the close callback without reimplementing geometry. Real browser tests cover focus wrapping/return, Escape, outside click, short-height scrolling and reachable final controls. Server actions and permission checks remain unchanged.

Broader customer tests exposed a pre-existing stale-founder-decision mismatch: the UI retains disabled input for copying while the old test expects automatic close/refresh. It reproduces unchanged on the pre-Dialog Table branch. Do not count that test as passed or silently rewrite its assertion under a structural migration.
