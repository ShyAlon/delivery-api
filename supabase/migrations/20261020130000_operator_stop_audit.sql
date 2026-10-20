-- Operator stop audit. Lets the signed-in operator read run-stop audit rows, and nothing else new.
--   1. registers the one operator account (by email, below) in the operator registry and enables it
--   2. lets the read-only operator database role see only audit rows whose action is 'run.stopped'
--   3. lets the app's database login use that role
-- Change only the email on the next line, and only if it is not yours. Run docs/operator-stop-audit-check.sql after.
DO $$
DECLARE
  operator_email CONSTANT text := 'shy.alon@gmail.com';
  operator_id uuid;
  other uuid;
BEGIN
  SELECT id INTO operator_id FROM auth.users WHERE lower(email) = lower(operator_email);
  IF operator_id IS NULL THEN
    IF EXISTS (SELECT 1 FROM auth.users) THEN
      RAISE EXCEPTION 'No Requi.re account has the email %. Nothing was changed.', operator_email;
    END IF;
    RAISE NOTICE 'This database has no accounts yet, so no operator was registered.';
    RETURN;
  END IF;
  SELECT subject INTO other FROM public.operator_principals WHERE enabled AND subject <> operator_id;
  IF other IS NOT NULL THEN
    RAISE EXCEPTION 'A different operator account is already enabled. Nothing was changed.';
  END IF;
  INSERT INTO public.operator_principals(subject, registry_version, capabilities, enabled)
  VALUES (operator_id, 'ops-v1', ARRAY['analytics.read','sessions.read','debug.read','logs.read'], true)
  ON CONFLICT (subject) DO UPDATE SET registry_version = 'ops-v1', enabled = true,
    capabilities = ARRAY['analytics.read','sessions.read','debug.read','logs.read'];
END $$;

GRANT SELECT ON public.audit_log TO requi_operator_read;
DROP POLICY IF EXISTS operator_stop_audit_read ON public.audit_log;
CREATE POLICY operator_stop_audit_read ON public.audit_log FOR SELECT TO requi_operator_read
  USING (action = 'run.stopped' AND public.requi_operator_read_allowed('logs.read'));

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'delivery_app') THEN
    GRANT requi_operator_read TO delivery_app WITH INHERIT FALSE, SET TRUE;
  ELSE
    RAISE NOTICE 'No delivery_app login exists here; grant requi_operator_read to the app login by hand.';
  END IF;
END $$;
