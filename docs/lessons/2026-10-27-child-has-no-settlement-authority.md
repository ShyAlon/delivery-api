# A child cannot be the source of its own provider usage

Moving reserve/start/settle messages alone leaves the child owning HTTP and
usage claims. Parent must own exact serialized request dispatch, raw terminal
usage, durable bytes and settlement. No provider URL/headers/price/release
fields belong in the private child request. Unknown outcome closes the
channel and pauses, never retries. Staged result is durable before delivery.

The missing broker and compute modules failed tests first. Engine's stale lazy
model regression failed before cache scoping was fixed. Tests use fake HTTP,
private local sockets/processes and disposable Postgres only. Default path and
paid subprocess refusal remain unchanged until full cutover tests pass.
