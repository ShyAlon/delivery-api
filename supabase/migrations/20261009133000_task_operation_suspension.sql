-- Task-operation-only suspension. No operator mutation grants/procedures.
-- Absence is active with version zero; state changes require reviewed CAS/audit.
CREATE TABLE public.user_task_policy (
 owner_id UUID PRIMARY KEY REFERENCES auth.users(id),
 suspended BOOLEAN NOT NULL,
 version BIGINT NOT NULL CHECK(version>0),
 updated_at DOUBLE PRECISION NOT NULL CHECK(updated_at>=0 AND updated_at<'Infinity'::double precision)
);
ALTER TABLE public.user_task_policy ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_task_policy FORCE ROW LEVEL SECURITY;
CREATE POLICY own_task_policy_read ON public.user_task_policy FOR SELECT TO requi_backend
 USING(public.requi_session_active() AND owner_id=auth.uid());
REVOKE ALL ON public.user_task_policy FROM PUBLIC,authenticated,requi_backend,requi_operator_read,requi_operator_usage;
GRANT SELECT ON public.user_task_policy TO requi_backend;

CREATE TABLE public.user_task_policy_changes (
 change_id UUID PRIMARY KEY,
 request_key TEXT NOT NULL UNIQUE CHECK(length(request_key) BETWEEN 1 AND 200),
 owner_id UUID NOT NULL REFERENCES auth.users(id),
 operator_subject UUID NOT NULL REFERENCES auth.users(id),
 registry_version TEXT NOT NULL CHECK(length(registry_version) BETWEEN 1 AND 200),
 review_digest TEXT NOT NULL CHECK(review_digest ~ '^[0-9a-f]{64}$'),
 approval_reference TEXT NOT NULL CHECK(length(approval_reference) BETWEEN 1 AND 500),
 reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 500),
 before_suspended BOOLEAN NOT NULL,
 after_suspended BOOLEAN NOT NULL,
 before_version BIGINT NOT NULL CHECK(before_version>=0),
 after_version BIGINT NOT NULL CHECK(after_version>0),
 applied_at DOUBLE PRECISION NOT NULL CHECK(applied_at>=0 AND applied_at<'Infinity'::double precision),
 CHECK(before_suspended<>after_suspended),
 CHECK(after_version::numeric=before_version::numeric+1)
);
ALTER TABLE public.user_task_policy_changes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_task_policy_changes FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.user_task_policy_changes FROM PUBLIC,authenticated,requi_backend,requi_operator_read,requi_operator_usage;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
  REVOKE ALL ON public.user_task_policy,public.user_task_policy_changes FROM anon;
 END IF;
END $$;
-- No defaults suspending anybody, no backfill, no auth session revocation.
-- Apply must use the shared owner advisory transaction lock before CAS/evidence.
