# Ollama choice is not readiness

The owner wants Ollama selectable even when it is down or its default model is not loaded. Route-list GET combined paid/global readiness, reviewed pins and an eager model-list preflight into a disabled local card. The user could not ask Start to report the actual failure.

Keep the local choice available and mark readiness as checked_on_start. Optional installed-model discovery supplies picker choices only, never readiness; lookup failure cannot disable the local choice. Start still validates the real model/connection and reviewed admission binding before it creates an interview; no execution fence or money check is bypassed. Child preflight translates known UserFacingError into the existing typed protocol error instead of leaking an unhandled exception.

Fail-first tests cover both account modes with no gateway and an unreachable engine. Signed-in browser exercises selectable Ollama, enabled Start, actual typed missing-model failure, enabled retry and unchanged task state. This is not a startup/admission redesign: missing installed runtime or reviewed local pin can still refuse Start, and that boundary is separate work.
