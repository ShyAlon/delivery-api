-- Explicit payment visibility capability, no enabled operator or membership.
ALTER TABLE public.operator_principals DROP CONSTRAINT operator_principals_capabilities_check;
ALTER TABLE public.operator_principals ADD CONSTRAINT operator_principals_capabilities_check
 CHECK(capabilities <@ ARRAY['analytics.read','sessions.read','debug.read','logs.read','usage.read','payments.read']::text[]);
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='requi_operator_payment_read') THEN
  CREATE ROLE requi_operator_payment_read NOLOGIN NOSUPERUSER NOBYPASSRLS;
 END IF;
END $$;
GRANT USAGE ON SCHEMA public TO requi_operator_payment_read;
GRANT EXECUTE ON FUNCTION public.requi_operator_read_allowed(TEXT,TEXT) TO requi_operator_payment_read;
GRANT SELECT(purchase_id,owner_id,processor,merchant_account,live_mode,offer_version,offer_id,currency,amount_minor,credit_units,units_per_credit,state,credit_posted,created_at,updated_at)
 ON public.payment_purchases TO requi_operator_payment_read;
GRANT SELECT(purchase_id,owner_id,processor,merchant_account,live_mode,amount_minor,currency,credit_units,units_per_credit,posted_at,processor_fee_minor,net_received_minor)
 ON public.payment_credits TO requi_operator_payment_read;
CREATE POLICY operator_payment_purchase_read ON public.payment_purchases FOR SELECT TO requi_operator_payment_read
 USING(public.requi_operator_read_allowed('payments.read'));
CREATE POLICY operator_payment_credit_read ON public.payment_credits FOR SELECT TO requi_operator_payment_read
 USING(public.requi_operator_read_allowed('payments.read'));
-- No event payloads, signed headers, request keys, order/capture IDs, secrets,
-- mutations, user browser grants, default registry capability or memberships.
