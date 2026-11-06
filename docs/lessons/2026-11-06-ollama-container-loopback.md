# A selected local route must be validated the way the container will see it

The preview passed validation with no OLLAMA_BASE_URL (or a loopback one), but
inside the container localhost is the container itself, so the Ollama route
failed only when the owner picked it in the UI, with a message advising him to
start an already-running host service. validate now refuses a missing,
unparseable or loopback OLLAMA_BASE_URL whenever the ollama route is selected
in REQUI_PIN_PROVIDERS, naming the exact value for native Mac Ollama
(http://host.docker.internal:11434/v1). Non-Ollama routes need nothing. This
mirrors the rule 681 added to the local-PG launcher; the two launchers now
share the same container-loopback semantics.
