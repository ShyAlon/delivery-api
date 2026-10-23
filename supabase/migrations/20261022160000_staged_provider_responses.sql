-- Staged provider responses (PR B: paid-call result durability).
-- One row per LOGICAL provider call. The logical identity is
-- (session, generation, execution_point, node, stage, item, ordinal):
--   execution_point - the durable checkpoint id the graph execution resumed
--     from ('bootstrap' before the first checkpoint). A crash-resume pins the
--     published checkpoint and re-executes under the same point; a NEW turn
--     resumes from a NEW committed checkpoint, so an intentional repeat of an
--     identical prompt is a NEW row (a new paid call) while a crash-replay of
--     the same logical call hits the same row.
--   node - the graph node issuing the call (two parallel nodes sharing an
--     execution point cannot collide).
--   ordinal - position of the call within that node execution for stage+item.
-- Lifecycle: INSERTed at dispatch (response NULL) -> bound to the ledger attempt ->
-- completed with the raw provider result + measured usage BEFORE settle/interpretation.
-- A row stuck dispatched-without-response is an UNKNOWN outcome: pause and reconcile,
-- never blind-rerun a possibly-charged call. No DELETE grant, ever.
-- Immutability is enforced BY THE DATABASE (trigger below): identity fields,
-- an established attempt binding, and a completed raw response can never be
-- overwritten through the backend role's UPDATE grant. The customer-facing
-- `authenticated` role has no grant at all: a tenant cannot read or forge the
-- authoritative raw usage/result rows.
CREATE TABLE staged_provider_responses (
  owner_id UUID NOT NULL REFERENCES auth.users(id),
  session_id TEXT NOT NULL,
  generation INTEGER NOT NULL,
  execution_point TEXT NOT NULL DEFAULT 'bootstrap',
  node TEXT NOT NULL DEFAULT '',
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
  PRIMARY KEY (session_id, generation, execution_point, node, stage, item, ordinal)
);

CREATE OR REPLACE FUNCTION public.staged_provider_responses_guard() RETURNS trigger
LANGUAGE plpgsql AS $guard$
BEGIN
  IF NEW.owner_id IS DISTINCT FROM OLD.owner_id
     OR NEW.session_id IS DISTINCT FROM OLD.session_id
     OR NEW.generation IS DISTINCT FROM OLD.generation
     OR NEW.execution_point IS DISTINCT FROM OLD.execution_point
     OR NEW.node IS DISTINCT FROM OLD.node
     OR NEW.stage IS DISTINCT FROM OLD.stage
     OR NEW.item IS DISTINCT FROM OLD.item
     OR NEW.ordinal IS DISTINCT FROM OLD.ordinal
     OR NEW.request_sha256 IS DISTINCT FROM OLD.request_sha256
     OR NEW.provider IS DISTINCT FROM OLD.provider
     OR NEW.model IS DISTINCT FROM OLD.model
     OR NEW.dispatched_at IS DISTINCT FROM OLD.dispatched_at THEN
    RAISE EXCEPTION 'staged_provider_responses: identity fields are immutable';
  END IF;
  IF OLD.attempt_id IS NOT NULL AND NEW.attempt_id IS DISTINCT FROM OLD.attempt_id THEN
    RAISE EXCEPTION 'staged_provider_responses: an established attempt binding is immutable';
  END IF;
  IF OLD.response IS NOT NULL
     AND (NEW.response IS DISTINCT FROM OLD.response
          OR NEW.usage IS DISTINCT FROM OLD.usage
          OR NEW.completed_at IS DISTINCT FROM OLD.completed_at) THEN
    RAISE EXCEPTION 'staged_provider_responses: a completed response is immutable';
  END IF;
  IF OLD.response IS NULL AND NEW.response IS NOT NULL AND NEW.completed_at IS NULL THEN
    RAISE EXCEPTION 'staged_provider_responses: completion requires completed_at';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE TRIGGER staged_provider_responses_guard
  BEFORE UPDATE ON staged_provider_responses
  FOR EACH ROW EXECUTE FUNCTION public.staged_provider_responses_guard();

ALTER TABLE staged_provider_responses ENABLE ROW LEVEL SECURITY;
ALTER TABLE staged_provider_responses FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON staged_provider_responses TO requi_backend
 USING (public.requi_session_active() AND owner_id = (select auth.uid()))
 WITH CHECK (public.requi_session_active() AND owner_id = (select auth.uid()));
REVOKE ALL ON staged_provider_responses FROM PUBLIC, authenticated;
-- UPDATE is required for the two lifecycle transitions (attempt binding, completion);
-- the trigger above constrains those transitions at the database layer.
GRANT SELECT, INSERT, UPDATE ON staged_provider_responses TO requi_backend;
