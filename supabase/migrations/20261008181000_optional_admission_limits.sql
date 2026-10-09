-- Explicit NULL means operator-selected disabled, not an implicit default.
-- Prepaid retail balance enforcement remains a separate required boundary.
ALTER TABLE public.admission_limits
 ALTER COLUMN expense_limit DROP NOT NULL,
 ALTER COLUMN retail_limit DROP NOT NULL,
 ALTER COLUMN attempt_expense_max DROP NOT NULL,
 ALTER COLUMN attempt_retail_max DROP NOT NULL,
 ALTER COLUMN rate_max DROP NOT NULL;
