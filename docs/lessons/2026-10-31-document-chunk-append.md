# Appending a log must write the new bytes only

Whole-document append copied and rehashed history for every log write. Store ordered epoch-scoped chunks with per-chunk SHA256 and bounded byte/count metadata under the existing worker/lease transaction. Base overwrite/clear increments an epoch; old chunks cannot reappear. Explicit reads/captures assemble exact ordered bytes and verify every part plus sequence length. Append no longer reads old blobs into Python. Full verification belongs on explicit read/export, not each append.

Schema is coordinated with the interview document reader/writer; both runtimes need the reviewed rollout. Existing documents are the base, not imported or rewritten. Old epochs remain evidence and add storage until separately reviewed retention work; do not silently delete history. Parent Stop/claim/generation fences remain unchanged, no new write grant or live rollout.
