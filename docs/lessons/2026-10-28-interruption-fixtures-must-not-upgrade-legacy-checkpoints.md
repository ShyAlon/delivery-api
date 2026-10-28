# Interruption fixtures must not upgrade legacy checkpoints

A shutdown-interrupted current run and an unbound historical checkpoint are not
the same resume authority. Keep resumable running state for the former,and
incompatibility/no-text-replay assertions for the latter when porting storage.
Do not manufacture a session binding to make a PG fixture pass.
