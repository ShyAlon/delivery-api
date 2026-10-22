-- Artifact manifest authority (PR B: DB artifact authority).
--   1. task_artifacts becomes the manifest of record: every artifact row carries
--      measured byte_size, content sha256, publication state and the validator
--      verdict recorded at publish time. Web reads serve from these rows instead of
--      re-scanning and re-validating the session filesystem on every request.
--   2. artifact_bytes stores bounded artifact bodies in-database, tenant-owned and
--      content-addressed within the tenant: dedup never crosses tenants, so a
--      collision cannot leak or oracle another tenant's content. Rows are immutable
--      (SELECT/INSERT only); a same-hash different-bytes insert conflicts loudly.
--   3. Publication is a compare-and-swap state machine: any number of staged rows,
--      at most one published row per (task, generation, phase, name) - a partial
--      unique index - so revisions stage freely and publishing is one atomic
--      demote-and-promote update, never blocked by the key.
-- Existing task_artifacts RLS (tenant_isolation, FORCE) covers the new columns.
ALTER TABLE task_artifacts
  ADD COLUMN byte_size BIGINT,
  ADD COLUMN sha256 TEXT,
  ADD COLUMN publication_state TEXT NOT NULL DEFAULT 'published'
    CHECK (publication_state IN ('staged','published','superseded')),
  ADD COLUMN published_at DOUBLE PRECISION,
  ADD COLUMN writer_instance TEXT,
  ADD COLUMN validator TEXT,
  -- Provenance of the validator implementation that produced the verdict: package
  -- version or schema fingerprint of the actual validation entry point. 'unknown' is
  -- an explicit marker (e.g. backfilled legacy verdicts), never a silent "latest".
  ADD COLUMN validator_version TEXT,
  ADD COLUMN valid BOOLEAN,
  ADD COLUMN validation_error TEXT;
-- At most one PUBLISHED row per logical artifact; staged/superseded rows are
-- unconstrained so revision loops never collide with the published pointer.
CREATE UNIQUE INDEX task_artifacts_one_published
  ON task_artifacts(task_id, generation, phase_key, name)
  WHERE publication_state = 'published';
CREATE TABLE artifact_bytes (
  owner_id UUID NOT NULL REFERENCES auth.users(id),
  sha256 TEXT NOT NULL,
  bytes BYTEA NOT NULL,
  byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
  PRIMARY KEY (owner_id, sha256)
);
ALTER TABLE artifact_bytes ENABLE ROW LEVEL SECURITY;
ALTER TABLE artifact_bytes FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON artifact_bytes TO requi_backend
 USING (public.requi_session_active() AND owner_id = (select auth.uid()))
 WITH CHECK (public.requi_session_active() AND owner_id = (select auth.uid()));
REVOKE ALL ON artifact_bytes FROM PUBLIC, authenticated;
GRANT SELECT, INSERT ON artifact_bytes TO requi_backend;
-- 4. Readiness marker: one row per (task, generation) marks that the manifest holds a
--    COMPLETE published revision for that generation. Producer and backfill publish
--    the full validated artifact set and mark readiness in one transaction; manifest
--    reads require the marker, so a partial manifest can never quietly replace the
--    legacy projection. Re-marks (revised publication of the same generation) update
--    marked_at.
CREATE TABLE artifact_manifest_ready (
  owner_id UUID NOT NULL REFERENCES auth.users(id),
  task_id TEXT NOT NULL,
  generation INTEGER NOT NULL,
  marked_at DOUBLE PRECISION NOT NULL,
  writer_instance TEXT,
  PRIMARY KEY (task_id, generation)
);
ALTER TABLE artifact_manifest_ready ENABLE ROW LEVEL SECURITY;
ALTER TABLE artifact_manifest_ready FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON artifact_manifest_ready TO requi_backend
 USING (public.requi_session_active() AND owner_id = (select auth.uid()))
 WITH CHECK (public.requi_session_active() AND owner_id = (select auth.uid()));
REVOKE ALL ON artifact_manifest_ready FROM PUBLIC, authenticated;
GRANT SELECT, INSERT, UPDATE ON artifact_manifest_ready TO requi_backend;
