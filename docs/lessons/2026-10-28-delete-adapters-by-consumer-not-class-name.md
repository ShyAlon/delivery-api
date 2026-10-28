# Delete adapters by consumer, not by class name

Removing a SQLite superclass does not prove its row/cursor/error bridge is unused. Inventory production lastrowid, positional rows, transaction hooks and exception consumers separately from placeholder/dialect SQL. Port the final real consumer, run with raw driver rows/cursors, and preserve a failing-first guard. Keep the remaining bridge while native operations or service helpers still require its query translation. Do not claim full SQLite retirement from a local acceptance slice.
