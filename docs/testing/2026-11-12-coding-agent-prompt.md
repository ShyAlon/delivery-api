# Explicit coding-agent prompt in final packages

The selected addition is one root Markdown file, CODING_AGENT_PROMPT.md, in the
same final ZIP. No extra implementation plan, model call, generator contract,
paid execution, schema change or settings change is required. It refers to
existing approved documents and their milestones/tests. Optional baseline and
validation paths appear only if those files are included.

The final packager validates the approved task state, producer session, blocking
findings, allowlisted names, bounds and source checksums before adding the
version-one deterministic template. README.md and spec/AGENTS.md remain unchanged.
The prompt and its hash become part of the package identity. An incoming prompt
must match the template exactly, not merely carry a matching supplied checksum.

Old retained packages without this member remain valid and are not rewritten.
Preparing a package again under the new code produces a newly identified ZIP;
its old download remains available. Use the existing final-package button after
installing the updated image. This change does not regenerate any owner's
existing package or access a live task during development.

Tests use disposable PostgreSQL and the real zero-model installed producer, with
actual create/download/repeat/tenant/tamper routes and the browser download flow.
They inspect the ZIP member and every manifest digest, deterministic repeat
identity, unchanged README/AGENTS bytes, optional file references, original input
immutability, rejected substituted prompts, and legacy retained-package reads.
