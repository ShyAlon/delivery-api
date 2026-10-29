# Deployed route rejection still needs a task owner

Two direct start-boundary tests created tasks using the default store without
tenant scope. Held PG-first correctly rejected the task creation before their
intended 422 route-policy check: 2 failed in 0.69s fail-first.

Use the existing scoped PG workspace for these two cases. The production
admission flag/gateway fixture and engine-call tripwires remain unchanged.
Keep the original expected 422 and cleanup. No assertion change, coverage loss,
production edit, skip, timeout relaxation or default-bootstrap flip.

Full local-Ollama module and cloud API require actual interview-engine source;
it is available at verified checkout f829cfb673d9c7ec49e4697708cf063a908e26fe.
The earlier missing-module failure was environment setup, not a product failure.
Hosted CI and full suite remain unverified.

Final selection: 69 passed in 3.80s current; 69 passed in 3.83s held PG-first.
