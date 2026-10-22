-- LangGraph checkpoint tables for session execution state (PR A: session ownership).
-- The migration owns the final langgraph v10 shape so the engine never runs DDL at
-- runtime: checkpoint_migrations is pre-recorded at the final v9 and saver.setup() is a no-op.
-- Indexes are created here; the library's CONCURRENTLY variants never execute.
CREATE TABLE checkpoint_migrations (
  v INTEGER PRIMARY KEY
);
-- langgraph records the last applied migration index (0-based): fully applied is v9.
INSERT INTO checkpoint_migrations(v) VALUES (9);
CREATE TABLE checkpoints (
  thread_id TEXT NOT NULL,
  checkpoint_ns TEXT NOT NULL DEFAULT '',
  checkpoint_id TEXT NOT NULL,
  parent_checkpoint_id TEXT,
  type TEXT,
  checkpoint JSONB NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}',
  PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
CREATE TABLE checkpoint_blobs (
  thread_id TEXT NOT NULL,
  checkpoint_ns TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL,
  version TEXT NOT NULL,
  type TEXT NOT NULL,
  blob BYTEA,
  PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);
CREATE TABLE checkpoint_writes (
  thread_id TEXT NOT NULL,
  checkpoint_ns TEXT NOT NULL DEFAULT '',
  checkpoint_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  idx INTEGER NOT NULL,
  channel TEXT NOT NULL,
  type TEXT,
  blob BYTEA NOT NULL,
  task_path TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
CREATE INDEX checkpoints_thread_id_idx ON checkpoints(thread_id);
CREATE INDEX checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id);
CREATE INDEX checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id);
-- Checkpoint rows are one session's execution state: the journal's tenant boundary,
-- backend-role only (users never read raw execution state; web reads go through the
-- manifest when PR B lands). No DELETE grant: nothing is auto-deleted, ever.
ALTER TABLE checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE checkpoints FORCE ROW LEVEL SECURITY;
ALTER TABLE checkpoint_blobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE checkpoint_blobs FORCE ROW LEVEL SECURITY;
ALTER TABLE checkpoint_writes ENABLE ROW LEVEL SECURITY;
ALTER TABLE checkpoint_writes FORCE ROW LEVEL SECURITY;
DO $$ DECLARE tab TEXT; BEGIN
  FOREACH tab IN ARRAY ARRAY['checkpoints','checkpoint_blobs','checkpoint_writes'] LOOP
    EXECUTE format('CREATE POLICY tenant_isolation ON public.%I TO requi_backend
      USING (public.requi_session_active() AND EXISTS(SELECT 1 FROM engine_sessions s
        WHERE s.session_id=%I.thread_id AND s.owner_id = (select auth.uid())))
      WITH CHECK (public.requi_session_active() AND EXISTS(SELECT 1 FROM engine_sessions s
        WHERE s.session_id=%I.thread_id AND s.owner_id = (select auth.uid())))',tab,tab,tab,tab);
    EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC, authenticated',tab);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.%I TO requi_backend',tab);
  END LOOP;
END $$;
REVOKE ALL ON checkpoint_migrations FROM PUBLIC, authenticated;
GRANT SELECT ON checkpoint_migrations TO requi_backend;

-- Tenancy guard: every public table carries forced RLS. The version ledger is
-- migration-owned and read-only for the runtime role (saver setup probes it).
ALTER TABLE checkpoint_migrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE checkpoint_migrations FORCE ROW LEVEL SECURITY;
CREATE POLICY checkpoint_migrations_read ON checkpoint_migrations
    FOR SELECT TO requi_backend USING (true);
