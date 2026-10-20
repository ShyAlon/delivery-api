-- Operator credit grants. Lets the one registered operator give an account free credits from the operator page.
--   1. allows the capability 'credits.allocate' in the operator registry and adds it to the enabled operator
--   2. two database functions, run with the migration owner's rights, that check the operator registry themselves:
--      preview (changes nothing) and grant (one transaction: evidence row, credit ledger entry, balance, admission row)
--   3. a database role that may call only those two functions, and lets the app's login use it
-- A grant is above 0 and at most 500 credits, needs a reason, and the same grant to the same account on the same UTC day is refused.
-- Run docs/operator-grant-credits-check.sql after.
DO $$
DECLARE c record;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint WHERE conrelid = 'public.operator_principals'::regclass AND contype = 'c'
           AND pg_get_constraintdef(oid) LIKE '%analytics.read%' LOOP
    EXECUTE format('ALTER TABLE public.operator_principals DROP CONSTRAINT %I', c.conname);
  END LOOP;
  ALTER TABLE public.operator_principals ADD CONSTRAINT operator_principals_capabilities_reviewed
    CHECK (capabilities <@ ARRAY['analytics.read','sessions.read','debug.read','logs.read','usage.read','payments.read','credits.allocate']::text[]);
  UPDATE public.operator_principals SET capabilities = array_append(capabilities, 'credits.allocate')
    WHERE enabled AND NOT ('credits.allocate' = ANY(capabilities));
END $$;

-- The functions run as the role applying this migration. FORCE ROW LEVEL SECURITY applies to that role too,
-- so it gets one policy per table it must touch; no other role gains anything from these policies.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['credit_accounts','credit_adjustments','admission_limits'] LOOP
    EXECUTE format('DROP POLICY IF EXISTS operator_grant_function ON public.%I', t);
    EXECUTE format('CREATE POLICY operator_grant_function ON public.%I FOR ALL TO %I USING (true) WITH CHECK (true)', t, current_user);
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION public.requi_operator_grant_preview(p_email text, p_credits numeric)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
  oid uuid; acc record; lim record; op uuid := auth.uid(); key text;
  units CONSTANT bigint := 1000000000;
BEGIN
  IF NOT public.requi_operator_read_allowed('credits.allocate', 'ops-v1') THEN RAISE EXCEPTION 'operator denied'; END IF;
  IF p_credits IS NULL OR p_credits <= 0 OR p_credits > 500 THEN RAISE EXCEPTION 'credits must be above 0 and at most 500'; END IF;
  SELECT id INTO oid FROM auth.users WHERE lower(email) = lower(trim(p_email));
  IF oid IS NULL THEN RETURN jsonb_build_object('found', false); END IF;
  SELECT * INTO acc FROM public.credit_accounts WHERE owner_id = oid;
  SELECT * INTO lim FROM public.admission_limits WHERE owner_id = oid;
  key := 'grant-' || oid || '-' || trim_scale(p_credits)::text || '-' || to_char(now() AT TIME ZONE 'UTC', 'YYYYMMDD');
  RETURN jsonb_build_object('found', true,
    'balance_credits', COALESCE(acc.balance_units, 0)::numeric / units,
    'after_credits', COALESCE(acc.balance_units, 0)::numeric / units + p_credits,
    'scale_ok', acc.owner_id IS NULL OR acc.units_per_credit = units,
    'already_granted_today', EXISTS (SELECT 1 FROM public.credit_adjustments WHERE request_key = key),
    'admission_row', CASE WHEN lim.owner_id IS NOT NULL THEN 'exists' WHEN oid = op THEN 'create own row with the 2 dollar 24 hour limit' ELSE 'create standard row' END);
END $$;

CREATE OR REPLACE FUNCTION public.requi_operator_grant_credits(p_email text, p_credits numeric, p_reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
  oid uuid; acc record; op uuid := auth.uid(); reg text; key text; delta bigint; now_s double precision := extract(epoch FROM clock_timestamp());
  units CONSTANT bigint := 1000000000; digest text; made text := 'exists';
BEGIN
  IF NOT public.requi_operator_read_allowed('credits.allocate', 'ops-v1') THEN RAISE EXCEPTION 'operator denied'; END IF;
  IF p_credits IS NULL OR p_credits <= 0 OR p_credits > 500 THEN RAISE EXCEPTION 'credits must be above 0 and at most 500'; END IF;
  IF p_credits <> round(p_credits, 6) THEN RAISE EXCEPTION 'at most 6 decimal places'; END IF;
  IF p_reason IS NULL OR length(trim(p_reason)) NOT BETWEEN 1 AND 400 THEN RAISE EXCEPTION 'a reason of 1 to 400 characters is required'; END IF;
  SELECT id INTO oid FROM auth.users WHERE lower(email) = lower(trim(p_email));
  IF oid IS NULL THEN RAISE EXCEPTION 'no account with that email'; END IF;
  SELECT registry_version INTO reg FROM public.operator_principals WHERE subject = op AND enabled;
  key := 'grant-' || oid || '-' || trim_scale(p_credits)::text || '-' || to_char(now() AT TIME ZONE 'UTC', 'YYYYMMDD');
  IF EXISTS (SELECT 1 FROM public.credit_adjustments WHERE request_key = key) THEN
    RETURN jsonb_build_object('granted', false, 'already_granted_today', true);
  END IF;
  INSERT INTO public.credit_accounts(owner_id, balance_units, units_per_credit, updated_at) VALUES (oid, 0, units, now_s) ON CONFLICT (owner_id) DO NOTHING;
  SELECT * INTO acc FROM public.credit_accounts WHERE owner_id = oid FOR UPDATE;
  IF acc.units_per_credit <> units THEN RAISE EXCEPTION 'the account uses a different credit scale'; END IF;
  delta := (p_credits * units)::bigint;
  digest := encode(sha256(convert_to(oid::text || '|' || trim_scale(p_credits)::text || '|' || to_char(now() AT TIME ZONE 'UTC', 'YYYYMMDD') || '|' || op::text, 'UTF8')), 'hex');
  INSERT INTO public.credit_adjustments(adjustment_id, request_key, owner_id, operator_subject, registry_version, review_digest, approval_reference, reason,
      delta_units, units_per_credit, before_balance_units, after_balance_units, expected_account_updated_at, applied_at)
    VALUES (gen_random_uuid(), key, oid, op, reg, digest, 'operator typed yes on the operator page at ' || to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
      trim(p_reason), delta, units, acc.balance_units, acc.balance_units + delta, acc.updated_at, now_s);
  PERFORM set_config('requi.ledger_kind', 'grant', true), set_config('requi.ledger_actor', 'operator:' || op::text, true),
          set_config('requi.ledger_reason', trim(p_reason), true), set_config('requi.ledger_reference', key, true);
  UPDATE public.credit_accounts SET balance_units = balance_units + delta, updated_at = now_s WHERE owner_id = oid;
  IF NOT EXISTS (SELECT 1 FROM public.admission_limits WHERE owner_id = oid) THEN
    IF oid = op THEN
      INSERT INTO public.admission_limits(owner_id, config_version, period_seconds, rate_window_seconds, concurrency_max, expense_limit) VALUES (oid, reg, 86400, 60, 3, 2000000000000);
      made := 'created own row with the 2 dollar 24 hour limit';
    ELSE
      INSERT INTO public.admission_limits(owner_id, config_version, period_seconds, rate_window_seconds, concurrency_max) VALUES (oid, reg, 86400, 60, 3);
      made := 'created standard row';
    END IF;
  END IF;
  RETURN jsonb_build_object('granted', true, 'before_credits', acc.balance_units::numeric / units, 'after_credits', (acc.balance_units + delta)::numeric / units, 'admission_row', made);
END $$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'requi_operator_grant') THEN
    CREATE ROLE requi_operator_grant NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;
REVOKE ALL ON FUNCTION public.requi_operator_grant_preview(text, numeric), public.requi_operator_grant_credits(text, numeric, text) FROM PUBLIC, authenticated;
GRANT USAGE ON SCHEMA public TO requi_operator_grant;
GRANT EXECUTE ON FUNCTION public.requi_operator_grant_preview(text, numeric), public.requi_operator_grant_credits(text, numeric, text) TO requi_operator_grant;
GRANT EXECUTE ON FUNCTION public.requi_operator_read_allowed(text, text) TO requi_operator_grant;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'delivery_app') THEN
    GRANT requi_operator_grant TO delivery_app WITH INHERIT FALSE, SET TRUE;
  ELSE
    RAISE NOTICE 'No delivery_app login exists here; grant requi_operator_grant to the app login by hand.';
  END IF;
END $$;
