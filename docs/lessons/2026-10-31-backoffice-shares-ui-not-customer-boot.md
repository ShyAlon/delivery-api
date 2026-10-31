# Share UI infrastructure, not unrelated authority

Back office's separate inline shell had diverged from the main app, and row actions kept growing sideways. Moving it into the customer boot would also start customer task/account/purchase behavior. Neither is the right boundary.

Reuse the main fonts, icon, theme, chrome, text-safe table rows, action lifecycle and redacted client evidence. Keep the private listener's exact API allowlist and existing Stop/Delete authorization. Operator tabs and one menu action list are shared UI modules, not another implementation of tenant, billing or Stop policy.

A row menu needs one close owner, keyboard focus restoration and viewport bounds. Changing a View trigger's text must not turn its permanent kebab into a Collapse button. Shared action logging needs a deliberate local-only mode on listeners without a client-log endpoint, not a broad new write proxy.

Tests use local fake service responses and disposable PG, never a live account. Both menu entrances, tabs, direct hashes, keyboard navigation, reduced motion, redacted evidence and separate Stop/Delete bodies are covered. Pixel inspection covers desktop, phone, menu, confirmation and Debug.

Preview owns only the Back office child it created. It refuses an occupied port instead of reusing or killing an unknown listener; it checks health and stops that child even when a failed browser smoke deliberately leaves the preview container for inspection.
