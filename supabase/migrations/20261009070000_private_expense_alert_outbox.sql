-- Private transport-neutral alert queue. No SMTP sender/recipient configured.
CREATE TABLE public.expense_alert_outbox (
 alert_key TEXT PRIMARY KEY, owner_id UUID NOT NULL REFERENCES auth.users(id),
 payload JSONB NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','sending','retry_pending','sent','operator_review')),
 attempts BIGINT NOT NULL DEFAULT 0 CHECK(attempts>=0), max_attempts BIGINT NOT NULL CHECK(max_attempts>0),
 created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL
);
ALTER TABLE public.expense_alert_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.expense_alert_outbox FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.expense_alert_outbox FROM PUBLIC,authenticated;
DO $$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN REVOKE ALL ON public.expense_alert_outbox FROM anon; END IF; END $$;
GRANT SELECT,INSERT,UPDATE ON public.expense_alert_outbox TO requi_backend;
CREATE POLICY owner_alerts ON public.expense_alert_outbox TO requi_backend
 USING(public.requi_session_active() AND owner_id=auth.uid())
 WITH CHECK(public.requi_session_active() AND owner_id=auth.uid());
