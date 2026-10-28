# A native reader facade is not dialect translation

Shared read helpers can preserve their reference-call defaults while the PG owner explicitly supplies native placeholders and an execute-only facade. Do not replace the old dialect bridge with a new silent query rewriter. Keep timing and transaction enforcement in the native executor. Verify exact remainder and replay behavior on real PG, and disclose unchanged UI assertion failures exposed by expanded testing rather than editing them into agreement.
