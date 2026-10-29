# Slot contention is not a live memory admission test

The three-run/two-slot test used the host's actual memory. On the current runner
only 507 MB was available, below the production 600 MB floor. The first run
finished, but the others correctly waited for memory instead of exercising slot
contention. Unchanged tests failed at the 30s guard on both bootstrap profiles:
1 failed / 5 passed in 30.52s current, and 30.87s held PG-first.

Supply ample host capacity to this one slot-contention test. Keep the real
memory guard, semaphore, queue notification and work in place. All original
assertions remain, including exact peak concurrency two and one plain waiting
line. No timeout change, guard bypass, skip, or production edit. The incidental
coverage of this host's live memory is removed; deliberate low-memory behavior
remains covered by test_memory_guard.py and test_container_memory.py.

Current selection of all three modules: 25 passed in 6.41s. Held PG-first
selection: 25 passed in 6.58s. Hosted CI and the full suite remain unverified. This
is not a fix or explanation for the separate cumulative browser navigation
slowness.
