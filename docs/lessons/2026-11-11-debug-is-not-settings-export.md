# Debug evidence must be allowlisted before reading

The owner ZIP contained a run-directory preview-container.env because the
collector read every regular file there. Redaction hid some settings, but the
free-text matcher missed prefixed INTERVIEW_ENGINE_TOKEN. Never use redaction
as permission to read settings. Reproduce with a fake secret and make opening
the unknown/settings path fail the regression, not merely test ZIP contents.

The run collector now reads only exact known evidence filenames and rejects
symlinks even for those names. Shared server/browser text scrubbers match
prefixed, suffixed, dashed and quoted sensitive assignment keys. Structured
redaction remains recursive. The sibling owned-local-preview collector already
uses explicit outputs; scrub its structured container-state subset as well.
No actual token is copied into source/tests/reports; only fake fixtures.
