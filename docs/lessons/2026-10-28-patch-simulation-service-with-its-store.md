# Patch the simulation service with its store

Changing app.store alone leaves a prebuilt Simulation object holding its original
database. Use a fresh per-test Simulation bound to the same scoped PG store and
prove identity in the fixture contract. Preserve local API policy explicitly:
storage migration does not authorize exposing replay import/execution in cloud.
