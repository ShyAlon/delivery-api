# Debug evidence must be readable in Debug

A package source and protected API endpoint are not a visible diagnostic view.
Operators should be able to open durable warning/error evidence inside Debug,
with task filter, bounds, truncation and read availability, without scripts or
SQL. Lazy read keeps routine dialog work bounded; failures offer a direct retry.
