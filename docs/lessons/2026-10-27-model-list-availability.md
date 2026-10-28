# A pinned fallback is not a fresh installed-model list

Keep an otherwise valid pinned route usable when installed-model lookup fails,
but carry explicit list availability/scope instead of silently presenting it as
the installed list. Task page shows a retry read without changing model selection.
Existing route-denial errors remain authoritative. The endpoint regression failed
first. Missing amounts, alternate providers and provider activation are not inferred.
