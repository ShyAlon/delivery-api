# Customer displays should read projections, not debit history

Task/job/phase cost reads repeatedly loaded contributions and rebuilt frozen rates. Maintain exact NUMERIC credits, historic-rate USD, posted units and evidence count in the contribution transaction. A zero-valued contribution is evidence; an empty task is not. Keep n to retain that distinction. Replay inserts nothing, rollback reverses projection and debit, and operator update/delete or explicit job reattribution changes the derived rows atomically. Public reads only round at the same six-decimal output boundary.

One-time schema backfill remains an explicit owner-reviewed operation, not a lazy repeated read. Additional derived storage and writes are the tradeoff. Do not treat frozen USD as current USD-per-credit, and do not alter charging/remainder semantics while optimizing a display.
