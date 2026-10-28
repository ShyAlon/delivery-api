# Remove unused fixture fallbacks after caller proof

When every review helper caller supplies PG,an optional SQLite fallback is dead
fixture behavior,not useful compatibility. Make the workspace required and guard
the helper signature/source. Keep the deliberate forbidden-SQLite assertion;its
class import is a guard,not an unmigrated receiver. Literal file counts alone
cannot distinguish those cases.
