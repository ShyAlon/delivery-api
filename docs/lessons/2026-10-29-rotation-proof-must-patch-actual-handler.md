# Rotation proof must patch the handler actually constructed

2026-10-29: stage logging now constructs _StageFileHandler, whose base class was
bound when observability imported. Patching the imported RotatingFileHandler
name later did not change that class, so the small-rotation test wrote one file
instead of exercising its three-file bound. Patch the constructed stage handler
with a small-budget factory that still instantiates the real stage handler.

Fail-first unchanged main: 1 failed, 4 passed (0.63s). Original three-file and
credential-scrubbing assertions retained. After: log safety/local usage evidence/
diagnostic evidence/runtime observability slice 27 passed current (2.02s),
27 passed held PG-first (1.37s), Python3.11.17. No production/bootstrap change,
assertion weakening or coverage loss. Hosted CI/full suite not run.
No deployment, external database, provider calls or spend.
