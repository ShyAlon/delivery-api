-- Session placement leases, fenced publication pointer and journal (PR A: session ownership).
--   1. engine_sessions gains the placement lease (placement_instance, lease_epoch,
--      lease_expires_at), staged control state and the published checkpoint pointer.
--      placement_instance holds a unique EXECUTION-INSTANCE identity (never a bare
--      hostname): two workers on one host must not be able to take each other's
--      live lease. Claim-before-serve and fencing rules live in the engine's
--      session store; this migration owns the schema.
--   2. engine_session_journal holds journal pages keyed by session and cursor, so a
--      resume on any host reads committed progress without files.
-- Existing engine_sessions RLS (tenant_isolation, FORCE) covers the new columns.
-- The new journal table gets the same tenant policy via its session row.
ALTER TABLE engine_sessions
  ADD COLUMN placement_instance TEXT,
  ADD COLUMN lease_epoch BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN lease_expires_at DOUBLE PRECISION NOT NULL DEFAULT 0,
  ADD COLUMN updated_at DOUBLE PRECISION,
  ADD COLUMN control JSONB,
  ADD COLUMN published_thread_id TEXT,
  ADD COLUMN published_checkpoint_ns TEXT NOT NULL DEFAULT '',
  ADD COLUMN published_checkpoint_id TEXT;
CREATE TABLE engine_session_journal (
  session_id TEXT NOT NULL REFERENCES engine_sessions(session_id),
  cursor BIGINT NOT NULL,
  page JSONB NOT NULL,
  created_at DOUBLE PRECISION NOT NULL,
  PRIMARY KEY(session_id, cursor)
);
ALTER TABLE engine_session_journal ENABLE ROW LEVEL SECURITY;
ALTER TABLE engine_session_journal FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON engine_session_journal TO authenticated
 USING (public.requi_session_active() AND EXISTS(SELECT 1 FROM engine_sessions s
   WHERE s.session_id=engine_session_journal.session_id AND s.owner_id = (select auth.uid())))
 WITH CHECK (public.requi_session_active() AND EXISTS(SELECT 1 FROM engine_sessions s
   WHERE s.session_id=engine_session_journal.session_id AND s.owner_id = (select auth.uid())));
GRANT SELECT, INSERT ON engine_session_journal TO authenticated;
-- Journal pages are immutable evidence: no UPDATE or DELETE for anyone, ever.
REVOKE UPDATE, DELETE ON engine_session_journal FROM authenticated;
