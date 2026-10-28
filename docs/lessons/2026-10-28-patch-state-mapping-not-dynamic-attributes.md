# Patch State mapping, not dynamic attributes, 2026-10-28

Starlette State stores application values in _state, not instance __dict__.
pytest9 monkeypatch bookkeeping can treat its attribute as originally absent and
delete it at teardown. The next fixture then loses app.state.store.

Patch the actual mapping with setitem so teardown restores its previous entry.
The existing sequence failed before this change and passes with pytest9 and latest
FastAPI/Starlette. No assertion changes, dependency downgrade or runtime code needed.
