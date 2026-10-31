# One private frame ingress and receiver lifecycle

C07: provider and compute parent channels copied socket/thread/context, bounded JSON-line parsing, private failure logging and shutdown. Share that mechanism once. Keep their byte limits and typed broker/compute schema checks separate; no global schema or permission is invented.

The compute channel's abort runs only after the receiver is quiescent, never while its finish handler may still settle. A shared close callback preserves this ordering. Malformed, duplicate-key, non-finite, overlong and unterminated frames close before the protocol handler. Existing exact compute/ledger/socket tests remain unchanged.
