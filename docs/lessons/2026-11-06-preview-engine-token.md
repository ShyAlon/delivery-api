# Owner-typed shared tokens are a recurring failure with no benefit

The image preview refused to start when INTERVIEW_ENGINE_TOKEN was missing from
the owner settings file, and its repair message named no command. The token only
authenticates the app's calls to its own engine children inside one owner
container, so a typed owner value adds setup failure modes without adding
authority.

The preview now generates a private token with secrets.token_urlsafe(32) when the
name is absent, appends it atomically to the private settings file (mode 600,
value never printed) and reuses it on later starts. Empty, short or placeholder
values still fail, and the message names the exact fix: delete the line and rerun
req preview --apply. Review mode (no --apply) stays read-only and names the apply
command instead of writing.

Provision/save flows in scripts/ops/settings.py filter unknown names out of any
save, so they could never persist this token; do not point owners at them for it.
