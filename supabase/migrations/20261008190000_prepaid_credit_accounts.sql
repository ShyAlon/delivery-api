-- Private prepaid accounting. Only the operator provisions accounts/credits.
CREATE TABLE public.credit_accounts (
 owner_id UUID PRIMARY KEY REFERENCES auth.users(id),
 balance_units BIGINT NOT NULL DEFAULT 0,
 units_per_credit BIGINT NOT NULL CHECK(units_per_credit>0),
 updated_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE public.credit_charges (
 attempt_id TEXT PRIMARY KEY REFERENCES public.provider_attempts(attempt_id),
 owner_id UUID NOT NULL REFERENCES auth.users(id),
 state TEXT NOT NULL CHECK(state IN ('pending','settled')),
 charged_units BIGINT,
 created_at DOUBLE PRECISION NOT NULL, settled_at DOUBLE PRECISION
);
ALTER TABLE public.credit_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.credit_accounts FORCE ROW LEVEL SECURITY;
ALTER TABLE public.credit_charges ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.credit_charges FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_credit_accounts ON public.credit_accounts TO requi_backend
 USING(public.requi_session_active() AND owner_id=auth.uid())
 WITH CHECK(public.requi_session_active() AND owner_id=auth.uid());
CREATE POLICY owner_credit_charges ON public.credit_charges TO requi_backend
 USING(public.requi_session_active() AND owner_id=auth.uid())
 WITH CHECK(public.requi_session_active() AND owner_id=auth.uid() AND EXISTS(
 SELECT 1 FROM public.provider_attempts p WHERE p.attempt_id=credit_charges.attempt_id AND p.owner_id=credit_charges.owner_id));
REVOKE ALL ON public.credit_accounts,public.credit_charges FROM PUBLIC,authenticated;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
  REVOKE ALL ON public.credit_accounts,public.credit_charges FROM anon;
 END IF;
END $$;
GRANT SELECT,UPDATE ON public.credit_accounts TO requi_backend;
GRANT SELECT,INSERT,UPDATE ON public.credit_charges TO requi_backend;
