# Worker-thread PG fixture reads enter tenant scope

ContextVars do not cross a raw ThreadPoolExecutor boundary. Enter the workspace
scope in the threaded HTTP task-read helper,not by bypassing tenant checks.
Keep responsiveness timeouts unchanged. Switch doubles must return the requested
engine session instead of hardcoded identity;repeated route requests still prove
one durable event. A synthetic real registry Program exercises concurrency
without a model/provider call.
