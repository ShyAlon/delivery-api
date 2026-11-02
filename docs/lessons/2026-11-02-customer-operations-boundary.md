# Customer pages stay customer pages, even for the owner

The Start page leaked operator readiness errors and a private operations link because /api/model-routes treated an admin actor as an operator surface. The UI trusted the raw error and repair fields too. A missing DeepSeek default was repeated on Ollama because one shared startup check closes the runtime, not because the local route needs a DeepSeek model.

Customer APIs never expose raw startup strings or repair destinations based on admin status. Customer route cards use a fixed plain vocabulary and ignore arbitrary API repair/error text, including stale servers. Operator evidence remains in the separate private workspace and evidence exports. Scan every shipped customer asset for private operations vocabulary and render a stale hostile response in browser tests. New customer routes must follow the same boundary.

Loading states use shared .loading motion on initial load and SPA navigation, with persistent accessible status text and reduced-motion fallback. A screenshot alone cannot prove motion: check computed animation in the real page as well.

This UI change does not invent a local execution bypass. Route-isolated zero-cost Ollama activation is separate runtime work, not a message substitution.
