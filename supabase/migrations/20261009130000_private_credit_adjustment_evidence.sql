-- Private future allocation evidence. No grant, procedure, route or adjustment.
CREATE TABLE public.credit_adjustments (
 adjustment_id UUID PRIMARY KEY,
 request_key TEXT NOT NULL UNIQUE CHECK(length(request_key) BETWEEN 1 AND 200),
 owner_id UUID NOT NULL REFERENCES auth.users(id),
 operator_subject UUID NOT NULL REFERENCES auth.users(id),
 registry_version TEXT NOT NULL CHECK(length(registry_version) BETWEEN 1 AND 200),
 review_digest TEXT NOT NULL CHECK(review_digest ~ '^[0-9a-f]{64}$'),
 approval_reference TEXT NOT NULL CHECK(length(approval_reference) BETWEEN 1 AND 500),
 reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 500),
 delta_units BIGINT NOT NULL CHECK(delta_units>0),
 units_per_credit BIGINT NOT NULL CHECK(units_per_credit>0),
 before_balance_units BIGINT NOT NULL,
 after_balance_units BIGINT NOT NULL,
 expected_account_updated_at DOUBLE PRECISION NOT NULL CHECK(expected_account_updated_at>=0 AND expected_account_updated_at<'Infinity'::double precision),
 applied_at DOUBLE PRECISION NOT NULL CHECK(applied_at>=0 AND applied_at<'Infinity'::double precision),
 CHECK(after_balance_units::numeric=before_balance_units::numeric+delta_units::numeric)
);
ALTER TABLE public.credit_adjustments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.credit_adjustments FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.credit_adjustments FROM PUBLIC,authenticated,requi_backend,requi_operator_read,requi_operator_usage;
-- No permissive policy, role membership, default approval or history backfill.
-- Approval references are evidence locators, not authorization by themselves.
