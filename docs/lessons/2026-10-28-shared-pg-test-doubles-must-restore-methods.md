# Shared PG test doubles must restore methods

A session-scoped store outlives a unit case. Directly replacing enqueue or an
operation guard leaks that fake into later cases. Use monkeypatch restoration,
and keep the existing mocked authority boundary explicit rather than claiming
real dispatch coverage. Separate SQLite files also hid duplicate phase-loop
names;make fixture titles distinct without changing tenant uniqueness rules.
