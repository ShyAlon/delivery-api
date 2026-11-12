# Host memory is not container memory

The app runs in a 3 GiB container but native Ollama runs on the Mac. A container
memory reading is not evidence about that host. Capacity must come from the
launcher host, tied to the known host endpoint, timestamped and allowed to expire.
The existing attended launcher owns the sampler. No new daemon, user setting or
per-model command is needed. A stopped launcher invalidates the snapshot.

A downloaded size is only a weight proxy. Context cache, recurrent state,
allocator/workspace overhead, concurrency and other applications all matter.
Qwen3.5's hybrid attention must not inherit Qwen3's all-layer KV formula. Estimated
fit is not a promise. Keep missing telemetry explicit, disable estimated-unfit
choices without changing context, and verify the actual loaded plan separately.
