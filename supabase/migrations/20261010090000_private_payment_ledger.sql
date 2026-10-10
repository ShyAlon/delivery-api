-- Private server payment journal. No public route, role membership or offers.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='requi_payment') THEN
  CREATE ROLE requi_payment NOLOGIN NOSUPERUSER NOBYPASSRLS;
 END IF;
END $$;
CREATE TABLE public.payment_purchases (
 purchase_id TEXT PRIMARY KEY CHECK(length(purchase_id) BETWEEN 1 AND 127),
 owner_id UUID NOT NULL REFERENCES auth.users(id),
 processor TEXT NOT NULL CHECK(length(processor) BETWEEN 1 AND 200),
 merchant_account TEXT NOT NULL CHECK(length(merchant_account) BETWEEN 1 AND 127),
 live_mode BOOLEAN NOT NULL,
 offer_version TEXT NOT NULL,offer_id TEXT NOT NULL,currency TEXT NOT NULL CHECK(currency='USD'),
 amount_minor BIGINT NOT NULL CHECK(amount_minor>0),credit_units BIGINT NOT NULL CHECK(credit_units>0),
 units_per_credit BIGINT NOT NULL CHECK(units_per_credit>0),
 intent_digest TEXT NOT NULL CHECK(intent_digest ~ '^[0-9a-f]{64}$'),
 create_request_id UUID,capture_request_id UUID,
 create_started_at DOUBLE PRECISION,capture_started_at DOUBLE PRECISION,
 order_id TEXT, capture_id TEXT,
 state TEXT NOT NULL DEFAULT 'awaiting_payment' CHECK(state IN ('awaiting_payment','fulfilled','operator_review')),
 credit_posted BOOLEAN NOT NULL DEFAULT FALSE,
 created_at DOUBLE PRECISION NOT NULL,updated_at DOUBLE PRECISION NOT NULL,
 UNIQUE(processor,merchant_account,live_mode,order_id),
 UNIQUE(processor,merchant_account,live_mode,capture_id),
 CHECK((create_request_id IS NULL)=(capture_request_id IS NULL)),
 CHECK(create_request_id IS NULL OR create_request_id<>capture_request_id)
);
CREATE TABLE public.payment_credits (
 payment_key TEXT PRIMARY KEY CHECK(payment_key ~ '^[0-9a-f]{64}$'),
 purchase_id TEXT NOT NULL UNIQUE REFERENCES public.payment_purchases(purchase_id),
 owner_id UUID NOT NULL REFERENCES auth.users(id),
 processor TEXT NOT NULL,merchant_account TEXT NOT NULL,live_mode BOOLEAN NOT NULL,
 capture_id TEXT NOT NULL,intent_digest TEXT NOT NULL,
 amount_minor BIGINT NOT NULL CHECK(amount_minor>0),currency TEXT NOT NULL CHECK(currency='USD'),
 credit_units BIGINT NOT NULL CHECK(credit_units>0),units_per_credit BIGINT NOT NULL CHECK(units_per_credit>0),
 posted_at DOUBLE PRECISION NOT NULL,
 processor_fee_minor BIGINT CHECK(processor_fee_minor>=0),net_received_minor BIGINT CHECK(net_received_minor>=0),
 CHECK((processor_fee_minor IS NULL)=(net_received_minor IS NULL)),
 CHECK(processor_fee_minor IS NULL OR processor_fee_minor::numeric+net_received_minor::numeric=amount_minor::numeric),
 UNIQUE(processor,merchant_account,live_mode,capture_id)
);
CREATE TABLE public.payment_events (
 processor TEXT NOT NULL,merchant_account TEXT NOT NULL,live_mode BOOLEAN NOT NULL,
 event_id TEXT NOT NULL,raw_digest TEXT NOT NULL CHECK(raw_digest ~ '^[0-9a-f]{64}$'),
 kind TEXT NOT NULL,purchase_id TEXT REFERENCES public.payment_purchases(purchase_id),
 result JSONB NOT NULL,recorded_at DOUBLE PRECISION NOT NULL,
 order_id TEXT,payment_id TEXT,action TEXT,
 PRIMARY KEY(processor,merchant_account,live_mode,event_id)
);
ALTER TABLE public.payment_purchases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.payment_purchases FORCE ROW LEVEL SECURITY;
ALTER TABLE public.payment_credits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.payment_credits FORCE ROW LEVEL SECURITY;
ALTER TABLE public.payment_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.payment_events FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.payment_purchases,public.payment_credits,public.payment_events FROM PUBLIC,authenticated,requi_backend,requi_payment;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
  REVOKE ALL ON public.payment_purchases,public.payment_credits,public.payment_events FROM anon;
 END IF;
END $$;
-- Ingest can journal private purchases/events, but cannot directly grant balance
-- or create/delete credit postings. No browser/server-user role inherits it.
GRANT USAGE ON SCHEMA public TO requi_payment;
GRANT SELECT,INSERT ON public.payment_purchases TO requi_payment;
GRANT UPDATE(create_request_id,capture_request_id,create_started_at,capture_started_at,order_id,state,updated_at) ON public.payment_purchases TO requi_payment;
GRANT SELECT ON public.payment_credits TO requi_payment;
GRANT SELECT,INSERT ON public.payment_events TO requi_payment;
CREATE POLICY private_payment_purchases ON public.payment_purchases TO requi_payment USING(true) WITH CHECK(true);
CREATE POLICY private_payment_credit_read ON public.payment_credits FOR SELECT TO requi_payment USING(true);
CREATE POLICY private_payment_events ON public.payment_events TO requi_payment USING(true) WITH CHECK(true);

CREATE FUNCTION public.requi_post_payment_credit(p_purchase TEXT,p_digest TEXT,p_payment_key TEXT,p_capture TEXT,p_now DOUBLE PRECISION,p_fee BIGINT,p_net BIGINT)
 RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE p public.payment_purchases%ROWTYPE; existing public.payment_credits%ROWTYPE; scale BIGINT;
BEGIN
 -- Payment ingestion authority is separate from an ordinary authenticated user.
 IF NOT pg_has_role(session_user,'requi_payment','MEMBER') THEN
  RAISE EXCEPTION 'private payment ingestion role required' USING ERRCODE='42501';
 END IF;
 IF (p_fee IS NULL)<>(p_net IS NULL) OR p_fee<0 OR p_net<0 THEN RAISE EXCEPTION 'invalid fee/net'; END IF;
 IF p_now<0 OR p_now>='Infinity'::double precision OR p_now IS NULL OR p_payment_key IS NULL OR p_capture IS NULL OR p_payment_key !~ '^[0-9a-f]{64}$' OR p_capture !~ '^[A-Za-z0-9_-]{1,127}$' THEN
  RAISE EXCEPTION 'invalid payment identity or timestamp';
 END IF;
 SELECT * INTO STRICT p FROM public.payment_purchases WHERE purchase_id=p_purchase FOR UPDATE;
 IF p_digest IS NULL OR p.intent_digest<>p_digest THEN RAISE EXCEPTION 'immutable purchase mismatch'; END IF;
 IF p_fee IS NOT NULL AND p_fee::numeric+p_net::numeric<>p.amount_minor::numeric THEN RAISE EXCEPTION 'fee/net mismatch'; END IF;
 IF p.state='operator_review' THEN RETURN FALSE; END IF;
 SELECT * INTO existing FROM public.payment_credits WHERE purchase_id=p_purchase OR payment_key=p_payment_key OR
  (processor=p.processor AND merchant_account=p.merchant_account AND live_mode=p.live_mode AND capture_id=p_capture) LIMIT 1;
 IF FOUND THEN
  IF existing.purchase_id<>p.purchase_id OR existing.payment_key<>p_payment_key OR existing.capture_id<>p_capture OR existing.intent_digest<>p_digest OR
     (p_fee IS NOT NULL AND existing.processor_fee_minor IS NOT NULL AND (existing.processor_fee_minor<>p_fee OR existing.net_received_minor<>p_net)) THEN
   RAISE EXCEPTION 'conflicting payment replay';
  END IF;
  RETURN FALSE;
 END IF;
 IF p.credit_posted OR (p.capture_id IS NOT NULL AND p.capture_id<>p_capture) THEN RAISE EXCEPTION 'conflicting purchase fulfillment'; END IF;
 -- Account row locking makes arithmetic/scale checks atomic with prepaid debit.
 SELECT units_per_credit INTO scale FROM public.credit_accounts WHERE owner_id=p.owner_id FOR UPDATE;
 IF NOT FOUND OR scale<>p.units_per_credit THEN RAISE EXCEPTION 'provisioned account scale required'; END IF;
 INSERT INTO public.payment_credits VALUES(p_payment_key,p.purchase_id,p.owner_id,p.processor,p.merchant_account,p.live_mode,p_capture,p_digest,
  p.amount_minor,p.currency,p.credit_units,p.units_per_credit,p_now,p_fee,p_net);
 UPDATE public.credit_accounts SET balance_units=balance_units+p.credit_units,updated_at=p_now WHERE owner_id=p.owner_id;
 UPDATE public.payment_purchases SET credit_posted=TRUE,state='fulfilled',capture_id=p_capture,updated_at=p_now WHERE purchase_id=p.purchase_id;
 RETURN TRUE;
END $$;
REVOKE ALL ON FUNCTION public.requi_post_payment_credit(TEXT,TEXT,TEXT,TEXT,DOUBLE PRECISION,BIGINT,BIGINT) FROM PUBLIC,authenticated,requi_backend;
GRANT EXECUTE ON FUNCTION public.requi_post_payment_credit(TEXT,TEXT,TEXT,TEXT,DOUBLE PRECISION,BIGINT,BIGINT) TO requi_payment;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
  REVOKE ALL ON FUNCTION public.requi_post_payment_credit(TEXT,TEXT,TEXT,TEXT,DOUBLE PRECISION,BIGINT,BIGINT) FROM anon;
 END IF;
END $$;

CREATE FUNCTION public.requi_payment_purchase_immutable() RETURNS TRIGGER
 LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 IF (NEW.purchase_id,NEW.owner_id,NEW.processor,NEW.merchant_account,NEW.live_mode,
     NEW.offer_version,NEW.offer_id,NEW.currency,NEW.amount_minor,NEW.credit_units,
     NEW.units_per_credit,NEW.intent_digest,NEW.created_at) IS DISTINCT FROM
    (OLD.purchase_id,OLD.owner_id,OLD.processor,OLD.merchant_account,OLD.live_mode,
     OLD.offer_version,OLD.offer_id,OLD.currency,OLD.amount_minor,OLD.credit_units,
     OLD.units_per_credit,OLD.intent_digest,OLD.created_at) THEN
  RAISE EXCEPTION 'immutable payment purchase fields';
 END IF;
 IF (OLD.order_id IS NOT NULL AND NEW.order_id IS DISTINCT FROM OLD.order_id) OR
    (OLD.capture_id IS NOT NULL AND NEW.capture_id IS DISTINCT FROM OLD.capture_id) OR
    (OLD.create_request_id IS NOT NULL AND NEW.create_request_id IS DISTINCT FROM OLD.create_request_id) OR
    (OLD.capture_request_id IS NOT NULL AND NEW.capture_request_id IS DISTINCT FROM OLD.capture_request_id) OR
    (OLD.create_started_at IS NOT NULL AND NEW.create_started_at IS DISTINCT FROM OLD.create_started_at) OR
    (OLD.capture_started_at IS NOT NULL AND NEW.capture_started_at IS DISTINCT FROM OLD.capture_started_at) OR
    (OLD.credit_posted AND NOT NEW.credit_posted) OR
    (OLD.state='operator_review' AND NEW.state<>'operator_review') THEN
  RAISE EXCEPTION 'payment binding/review cannot be reopened';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.requi_payment_purchase_immutable() FROM PUBLIC;
CREATE TRIGGER payment_purchase_immutable BEFORE UPDATE ON public.payment_purchases
 FOR EACH ROW EXECUTE FUNCTION public.requi_payment_purchase_immutable();
