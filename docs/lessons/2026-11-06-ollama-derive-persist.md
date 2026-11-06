# Derive and persist, but never override

For owner settings a container cannot do without, the pattern that survives
contact with the owner is: derive the one defensible value when the name is
ABSENT, append it atomically to the private file (mode 600, logged), and reuse
it afterward - generation for secrets (682's engine token), the documented
native-Mac default for locations (OLLAMA_BASE_URL when the ollama route is
pinned). An EXPLICIT value is never rewritten: loopback or malformed values
fail with the named fix, because silently overriding owner intent is the real
silent fallback. Review mode (no --apply) stays read-only and names the apply
command instead of writing.
