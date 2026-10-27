# 2026-10-27 - Availability means startable, and a marker is part of the test

1. The cloud /api/model-routes listed DeepSeek as available whenever execution
   was open. In an ollama-mode preview the key never reaches the container, so
   the UI offered a selectable route that failed at start - the exact
   dead-selectable-route trap a previous fix removed in the other direction.
   Availability must be computed from what a start actually needs: execution
   open AND the key the in-process engine bills with. A dead route says so,
   naming the missing variable (never the value).

2. test_local_clients_are_pinned_to_the_configured_origin imports
   interview_engine through the owner_module seam but carried no
   engine_integration marker, so the standard gate
   (scripts/ci/test-pr.sh: -m 'not engine_integration ...') could not deselect
   it and a plain delivery venv ran red on main. A test that needs an
   optional package is not done until its marker says so.
