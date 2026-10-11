-- Explicit audited non-billing of unknown failed-job compute. No public route.
ALTER TABLE public.retail_compute_segments DROP CONSTRAINT retail_compute_segments_state_check;
ALTER TABLE public.retail_compute_segments ADD CONSTRAINT retail_compute_segments_state_check
 CHECK(state IN ('pending','settled','unresolved','waived'));
ALTER TABLE public.retail_compute_segments ADD CONSTRAINT retail_compute_waived_unknown
 CHECK(state<>'waived' OR (deterministic_ms IS NULL AND settled_at IS NULL));
