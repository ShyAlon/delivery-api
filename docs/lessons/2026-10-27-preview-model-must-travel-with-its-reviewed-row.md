# 2026-10-27 - The preview's model must travel with its reviewed row

A preview on the ollama route showed "phi4" in the start UI and died at the
first model call: AdmissionDenied 'serialized request bound is unreviewed'.
Chain (provable from code): preview-exec pinned the reviewed $0 row for
PREVIEW_EXEC_OLLAMA_MODEL (phi4) but never wrote DELIVERY_OLLAMA_MODEL into
preview.env; the start POST carried no explicit initial model; the engine
session (session_models.resolve) then pinned its built-in default
qwen2.5-coder:14b for every stage; the reviewed local gateway knows only phi4,
so the scope_tiebreak call was refused and the rules fallback could not
classify the brief either -> "all providers failed for scope". The UI's shown
model (model_routes.DEFAULT_OLLAMA_MODEL) and the engine's fallback are two
independent defaults - keeping them aligned by hand drifts.

Fix: preview_ollama_env (lib.sh) writes DELIVERY_OLLAMA_MODEL with the same
$OLLAMA_MODEL that feeds pin-local.py - one variable, both consumers.

Two evidence gaps made this slower than it had to be:
- AdmissionDenied named neither the route nor the stage it missed (now it
  names provider/model/stage; test in test_admission_catalog.py).
- The engine->delivery error path serialized the failure as a bare
  "RuntimeError" class name, dropping str(e) - the detailed engine message
  ("all providers failed for scope: ...") existed only in the engine log
  section of the debug ZIP. (Engine-side fix belongs to the interview-engine
  repo; flagged separately.)
