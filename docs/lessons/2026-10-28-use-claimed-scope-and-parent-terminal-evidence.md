# Use claimed scope and parent terminal evidence

The claimed worker capability already supplies the correct task/session/job.
Bind it around validation, handler and completion/failure recording rather than
adding diagnostic DB lookups. This carries existing request reference when present.

A child whose stderr is intentionally discarded still needs a parent-side failure
record. Record safe supervisor exception class/frame and binding before reaping.
No claim that an observed crash distinguishes OOM from external kill without host
evidence. Cancellation stays control flow and does not manufacture provider state.
