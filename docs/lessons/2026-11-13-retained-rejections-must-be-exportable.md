# Retaining rejected output is not enough if Debug cannot retrieve it

The generator saves every rejected completion and a manifest containing the
validator reason, attempt and exact output path. The isolated child runtime
puts its private LLM sink at the session root. Therefore its rejection manifest
is rejected-llm-outputs/manifest.jsonl, not spec/llm-calls.jsonl. A Debug ZIP that
exports only phase logs can show a first warning and a generic final failure
while hiding the retained second rejection entirely.

Read the two known rejection directories through the existing owned PostgreSQL
document capability, and include the current generator intent/CONOPS inputs.
Do not substitute an old task-event copy of the founder register for the source
used by generation. Manifest paths are untrusted data: allow only direct raw
files in those known directories, never arbitrary owned keys or filesystem
reads. Keep the latest eight output keys across both roots and existing 2 MB
per-source / 12 MB total limits. Report omissions and retain existing credential
redaction. Exported project wording is diagnostic evidence, not an instruction.

The endpoint keeps its current account-debug capability and task-owner checks.
No share/upload, provider call, task retry, schema change or automatic recovery
is part of downloading this evidence. The export cannot recreate missing bytes;
if retained data is absent, say so rather than guessing the final rejection.

Prove the root-path case first, then traversal/outside/other-key rejection,
legacy paths, redaction and limits. Also use real owned PG documents through
the HTTP download, and prove another signed-in owner cannot read the task.
