# Deduplicate by durable identity, not by loading the event history

The task row lock already serializes repeated waiting-input observations. Keep it; replace full payload reads and Python parsing with a parameterized exact JSON input_id lookup and matching partial task/input/id index. Earliest existing event wins, preserving historical duplicates without deleting data. Keep quotes and tenant boundaries in real PG tests, independent-connection races, and EXPLAIN proof on 10k events. The index build remains owner-reviewed migration work.
