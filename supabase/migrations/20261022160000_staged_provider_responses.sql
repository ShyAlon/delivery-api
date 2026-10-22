-- Staged provider responses (PR B: paid-call result durability).
-- One row per LOGICAL provider call: (session, generation, stage, item, ordinal).
-- The ordinal is the position in the generation's deterministic call sequence for
-- that stage+item, so an intentional repeat of an identical prompt is a NEW row
-- (a new paid call) while a crash-replay of the same logical call hits the same row.
-- Lifecycle: INSERTed at dispatch (response NULL) -> bound to the ledger attempt ->
-- completed with the raw provider result + measured usage BEFORE settle/interpretation.
-- A row stuck dispatched-without-response is an UNKNOWN outcome: pause and reconcile,
-- never blind-rerun a possibly-charged call. No DELETE grant, ever.
CREATE TABLE staged_provider_responses (
  owner_id UUID NOT NULL REFERENCES auth.users(id),
  session_id TEXT NOT NULL,
  generation INTEGER NOT NULL,
  stage TEXT NOT NULL,
  item TEXT NOT NULL DEFAULT '',
  ordinal INTEGER NOT NULL,
  attempt_id TEXT,
  request_sha256 TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  dispatched_at DOUBLE PRECISION NOT NULL,
  response JSONB,
  usage JSONB,
  completed_at DOUBLE PRECISION,
  PRIMARY KEY (session_id, generation, stage, item, ordinal)
);
ALTER TABLE staged_provider_responses ENABLE ROW LEVEL SECURITY;
ALTER TABLE staged_provider_responses FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON staged_provider_responses TO requi_backend
 USING (public.requi_session_active() AND owner_id = (select auth.uid()))
 WITH CHECK (public.requi_session_active() AND owner_id = (select auth.uid()));
REVOKE ALL ON staged_provider_responses FROM PUBLIC, authenticated;
-- UPDATE is required for the two lifecycle transitions (attempt binding, completion);
-- the store enforces append-once semantics (NULL -> set only) in code, proven by tests.
GRANT SELECT, INSERT, UPDATE ON staged_provider_responses TO requi_backend;
