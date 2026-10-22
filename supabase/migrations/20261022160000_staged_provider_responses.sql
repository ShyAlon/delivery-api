-- Staged provider responses (PR B: paid-call result durability).
-- The raw provider response and its measured usage are staged durably BEFORE the
-- graph interprets them, keyed by the exact request (session, stage, item, request
-- hash). A crash after provider completion replays from the staged row instead of
-- re-calling and re-billing; only usable responses are staged (provider-empty or
-- truncated attempts are failures, never replayable results). Rows are immutable:
-- SELECT/INSERT only, a same-key different-payload insert conflicts loudly.
CREATE TABLE staged_provider_responses (
  owner_id UUID NOT NULL REFERENCES auth.users(id),
  session_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  item TEXT,
  request_sha256 TEXT NOT NULL,
  response JSONB NOT NULL,
  usage JSONB,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 1,
  created_at DOUBLE PRECISION NOT NULL,
  PRIMARY KEY (session_id, stage, request_sha256)
);
ALTER TABLE staged_provider_responses ENABLE ROW LEVEL SECURITY;
ALTER TABLE staged_provider_responses FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON staged_provider_responses TO requi_backend
 USING (public.requi_session_active() AND owner_id = (select auth.uid()))
 WITH CHECK (public.requi_session_active() AND owner_id = (select auth.uid()));
REVOKE ALL ON staged_provider_responses FROM PUBLIC, authenticated;
GRANT SELECT, INSERT ON staged_provider_responses TO requi_backend;
