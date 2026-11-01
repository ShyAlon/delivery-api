# A busy port needs an owner, not a guess

Preview refused a busy port but only said to stop something. Print read-only lsof COMMAND/PID and running Docker names/IDs/host port mappings before aborting. Match actual host bindings, not container-only ports. Give docker stop only for the fixed preview container name; foreign listeners need owner review. Never run a kill/stop while diagnosing, never print process arguments or container environment. Missing/permission-limited diagnostics remain explicitly unverified.
