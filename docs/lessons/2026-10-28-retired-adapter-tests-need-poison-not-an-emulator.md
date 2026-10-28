# Retired adapter tests need poison, not an emulator

After last production consumer proof, remove the adapter rather than carrying it for tests. Move old fences to a test-only poison object that always raises. Keep all behavioral assertions and prove the poison itself refuses execution. Move scoped direct-read tests to the native executor. A fake compatibility executor would preserve the technical debt and could silently let future fallback paths pass.
