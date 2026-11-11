-- Additive accounting foundation only. No route/gateway selects answer-v1 yet.
-- Trusted backend resolves configured E/rates; browsers cannot call these APIs.
-- No journal, allowance, Q, retry/call/deadline cap or per-attempt DB marker.
CREATE TABLE public.answer_charge_receipts (
 answer_id text PRIMARY KEY CHECK(length(answer_id) BETWEEN 1 AND 128),
 owner_id uuid NOT NULL REFERENCES auth.users(id),
 job_id text NOT NULL REFERENCES public.worker_jobs(job_id),
 session_id text NOT NULL, generation integer NOT NULL CHECK(generation>0),
 input_id text NOT NULL CHECK(length(input_id) BETWEEN 1 AND 240),
 input_digest text NOT NULL CHECK(input_digest ~ '^[0-9a-f]{64}$'),
 pricing jsonb NOT NULL, estimate_units bigint NOT NULL CHECK(estimate_units>0),
 units_per_credit bigint NOT NULL CHECK(units_per_credit=1000000000),
 state text NOT NULL CHECK(state IN ('pending','settled')),
 actual_units bigint CHECK(actual_units>=0), correction_units bigint,
 fractional_remainder numeric CHECK(fractional_remainder>=0 AND fractional_remainder<1),
 usage_manifest jsonb, overrun boolean, started_at timestamptz NOT NULL DEFAULT clock_timestamp(), settled_at timestamptz,
 UNIQUE(owner_id,session_id,generation,input_id),
 CHECK((state='settled')=(usage_manifest IS NOT NULL)),
 CHECK((state='settled')=(actual_units IS NOT NULL AND correction_units IS NOT NULL AND fractional_remainder IS NOT NULL AND overrun IS NOT NULL AND settled_at IS NOT NULL))
);
CREATE INDEX answer_charge_receipts_owner_time ON public.answer_charge_receipts(owner_id,started_at);
ALTER TABLE public.answer_charge_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.answer_charge_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY answer_receipts_owner ON public.answer_charge_receipts TO requi_backend
 USING(public.requi_session_active() AND owner_id=auth.uid())
 WITH CHECK(public.requi_session_active() AND owner_id=auth.uid() AND EXISTS(
 SELECT 1 FROM public.worker_jobs w WHERE w.job_id=answer_charge_receipts.job_id AND w.owner_id=answer_charge_receipts.owner_id));

-- Final usage only: uniqueness prevents charging the same attempt in two answers.
-- These rows are inserted in the final correction transaction, never before send.
CREATE TABLE public.answer_charge_usage (
 attempt_id text PRIMARY KEY CHECK(length(attempt_id) BETWEEN 1 AND 240),
 answer_id text NOT NULL REFERENCES public.answer_charge_receipts(answer_id),
 owner_id uuid NOT NULL REFERENCES auth.users(id),
 evidence jsonb NOT NULL
);
ALTER TABLE public.answer_charge_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.answer_charge_usage FORCE ROW LEVEL SECURITY;
CREATE POLICY answer_usage_owner ON public.answer_charge_usage TO requi_backend
 USING(public.requi_session_active() AND owner_id=auth.uid())
 WITH CHECK(public.requi_session_active() AND owner_id=auth.uid() AND EXISTS(
 SELECT 1 FROM public.answer_charge_receipts r WHERE r.answer_id=answer_charge_usage.answer_id AND r.owner_id=answer_charge_usage.owner_id));

CREATE FUNCTION public.requi_answer_start(p_answer text,p_job text,p_input text,p_digest text,p_pricing jsonb)
RETURNS jsonb LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE oid uuid:=auth.uid(); a public.credit_accounts; r public.answer_charge_receipts; j public.worker_jobs;
 e numeric; rate numeric; k text; snap jsonb;
BEGIN
 IF oid IS NULL OR NOT public.requi_session_active() THEN RAISE EXCEPTION 'active authenticated owner required'; END IF;
 IF p_answer IS NULL OR length(p_answer) NOT BETWEEN 1 AND 128 OR p_input IS NULL OR length(p_input) NOT BETWEEN 1 AND 240 OR
    p_digest IS NULL OR p_digest !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'invalid answer identity'; END IF;
 SELECT * INTO a FROM public.credit_accounts WHERE owner_id=oid FOR UPDATE;
 IF NOT FOUND OR a.units_per_credit<>1000000000 THEN RAISE EXCEPTION 'exact prepaid account required'; END IF;
 SELECT * INTO j FROM public.worker_jobs WHERE job_id=p_job AND owner_id=oid;
 IF NOT FOUND THEN RAISE EXCEPTION 'owned answer job required'; END IF;
 SELECT * INTO r FROM public.answer_charge_receipts WHERE answer_id=p_answer OR
   (owner_id=oid AND session_id=j.session_id AND generation=j.generation AND input_id=p_input);
 IF FOUND THEN
   IF r.owner_id<>oid OR r.session_id<>j.session_id OR r.generation<>j.generation OR r.input_id<>p_input OR r.input_digest<>p_digest THEN RAISE EXCEPTION 'conflicting answer replay'; END IF;
   -- Current environment/config changes must not reprice an existing receipt.
   RETURN to_jsonb(r);
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.worker_jobs w JOIN public.tasks t ON t.task_id=w.task_id AND t.owner_id=w.owner_id
   WHERE w.job_id=p_job AND w.owner_id=oid AND w.generation=t.generation AND t.deleted_at IS NULL
     AND w.auth_session_id=NULLIF(current_setting('request.jwt.claim.session_id',true),'')::uuid
     AND w.state IN ('pending','running')) THEN RAISE EXCEPTION 'current owned answer job required'; END IF;
 IF p_pricing IS NULL OR jsonb_typeof(p_pricing)<>'object' OR NOT(p_pricing ?& ARRAY['version','start_credits','estimate_units','rates']) OR
   p_pricing-ARRAY['version','start_credits','estimate_units','rates']<>'{}'::jsonb OR p_pricing->>'version' IS DISTINCT FROM 'answer-v1' OR
   jsonb_typeof(p_pricing->'start_credits')<>'string' OR jsonb_typeof(p_pricing->'estimate_units')<>'number' THEN RAISE EXCEPTION 'frozen answer pricing required'; END IF;
 e:=(p_pricing->>'estimate_units')::numeric;
 IF e<=0 OR e IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) OR e<>trunc(e) OR e>9223372036854775807 OR
    (p_pricing->>'start_credits')::numeric*1000000000<>e THEN RAISE EXCEPTION 'positive exact configured estimate required'; END IF;
 snap:=p_pricing->'rates';
 IF jsonb_typeof(snap)<>'object' OR NOT(snap ?& ARRAY['formula_version','units_per_credit','price_per_token','price_per_10ms','usd_per_credit']) OR
   snap-ARRAY['formula_version','units_per_credit','price_per_token','price_per_10ms','usd_per_credit']<>'{}'::jsonb OR
   snap->>'formula_version' IS DISTINCT FROM 'tokens-plus-compute-v1' OR snap->'units_per_credit' IS DISTINCT FROM '1000000000'::jsonb THEN RAISE EXCEPTION 'frozen retail formula required'; END IF;
 FOREACH k IN ARRAY ARRAY['price_per_token','price_per_10ms','usd_per_credit'] LOOP
   IF jsonb_typeof(snap->k)<>'string' THEN RAISE EXCEPTION 'exact decimal rates required'; END IF;
   rate:=(snap->>k)::numeric;
   IF rate<0 OR rate IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) OR
     (k='usd_per_credit' AND rate=0) OR (k<>'usd_per_credit' AND rate*1000000000<>trunc(rate*1000000000)) THEN RAISE EXCEPTION 'invalid frozen retail rate'; END IF;
 END LOOP;
 IF a.balance_units<e THEN RAISE EXCEPTION 'insufficient prepaid balance'; END IF;
 INSERT INTO public.answer_charge_receipts(answer_id,owner_id,job_id,session_id,generation,input_id,input_digest,pricing,estimate_units,units_per_credit,state)
 VALUES(p_answer,oid,p_job,j.session_id,j.generation,p_input,p_digest,p_pricing,e::bigint,1000000000,'pending') RETURNING * INTO r;
 PERFORM set_config('requi.ledger_kind','debit',true),set_config('requi.ledger_actor','system:answer_accounting',true),
   set_config('requi.ledger_reason','answer estimate',true),set_config('requi.ledger_reference',p_answer,true);
 UPDATE public.credit_accounts SET balance_units=balance_units-e::bigint,updated_at=extract(epoch from clock_timestamp()) WHERE owner_id=oid;
 RETURN to_jsonb(r);
END $$;

CREATE FUNCTION public.requi_answer_finish(p_answer text,p_usage jsonb)
RETURNS jsonb LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE oid uuid:=auth.uid(); a public.credit_accounts; r public.answer_charge_receipts;
 input_n numeric:=0; output_n numeric:=0; duration numeric; item jsonb; n numeric; exact numeric; actual numeric; remainder numeric; after_balance numeric;
 carry numeric; part numeric; posted bigint; phase record; share numeric; remaining numeric; projected numeric:=0;
BEGIN
 IF oid IS NULL OR NOT public.requi_session_active() THEN RAISE EXCEPTION 'active authenticated owner required'; END IF;
 SELECT * INTO a FROM public.credit_accounts WHERE owner_id=oid FOR UPDATE;
 IF NOT FOUND OR a.units_per_credit<>1000000000 THEN RAISE EXCEPTION 'exact prepaid account required'; END IF;
 SELECT * INTO r FROM public.answer_charge_receipts WHERE answer_id=p_answer AND owner_id=oid FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'owned answer receipt unavailable'; END IF;
 IF r.state='settled' THEN
   IF r.usage_manifest IS DISTINCT FROM p_usage THEN RAISE EXCEPTION 'conflicting answer correction replay'; END IF;
   RETURN to_jsonb(r);
 END IF;
 IF p_usage IS NULL OR jsonb_typeof(p_usage)<>'object' OR NOT(p_usage ?& ARRAY['attempts','deterministic_ms']) OR
   p_usage-ARRAY['attempts','deterministic_ms']<>'{}'::jsonb OR jsonb_typeof(p_usage->'attempts')<>'array' OR
   jsonb_typeof(p_usage->'deterministic_ms')<>'string' THEN RAISE EXCEPTION 'exact normalized answer usage required'; END IF;
 duration:=(p_usage->>'deterministic_ms')::numeric;
 IF duration<0 OR duration IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric) THEN RAISE EXCEPTION 'invalid normalized answer usage'; END IF;
 carry:=a.fractional_debit_units;
 FOR item IN SELECT value FROM jsonb_array_elements(p_usage->'attempts') LOOP
   IF jsonb_typeof(item)<>'object' OR NOT(item ?& ARRAY['attempt_id','phase','input_tokens','output_tokens']) OR
      item-ARRAY['attempt_id','phase','input_tokens','output_tokens']<>'{}'::jsonb OR
      jsonb_typeof(item->'attempt_id')<>'string' OR length(item->>'attempt_id') NOT BETWEEN 1 AND 240 OR
      jsonb_typeof(item->'phase')<>'string' OR length(item->>'phase') NOT BETWEEN 1 AND 200 OR
      jsonb_typeof(item->'input_tokens')<>'number' OR jsonb_typeof(item->'output_tokens')<>'number' THEN RAISE EXCEPTION 'invalid attempt usage'; END IF;
   n:=(item->>'input_tokens')::numeric;
   IF n<0 OR n<>trunc(n) THEN RAISE EXCEPTION 'invalid input tokens'; END IF;
   input_n:=input_n+n;
   n:=(item->>'output_tokens')::numeric;
   IF n<0 OR n<>trunc(n) THEN RAISE EXCEPTION 'invalid output tokens'; END IF;
   output_n:=output_n+n;
   INSERT INTO public.answer_charge_usage(attempt_id,answer_id,owner_id,evidence)
     VALUES(item->>'attempt_id',p_answer,oid,item);
   -- Display evidence only. The single account correction below is the ONLY debit.
   part:=((item->>'input_tokens')::numeric+(item->>'output_tokens')::numeric)*
       (r.pricing->'rates'->>'price_per_token')::numeric*1000000000;
   posted:=floor(part+carry)::bigint;carry:=part+carry-posted;projected:=projected+posted;
   INSERT INTO public.retail_contributions VALUES(
     'answer-token:'||md5(jsonb_build_array(oid,r.job_id,p_answer,item->>'attempt_id')::text),oid,r.job_id,item->>'phase','tokens',
     (item->>'input_tokens')::bigint,(item->>'output_tokens')::bigint,0,r.pricing->'rates',part,posted,1000000000,
     md5(item::text||r.pricing::text),extract(epoch from clock_timestamp()));
 END LOOP;
 -- Preserve existing call-weighted phase compute attribution, including zero-call answers.
 remaining:=duration;
 FOR phase IN
   WITH counts AS (
     SELECT value->>'phase' AS name,count(*)::numeric AS n FROM jsonb_array_elements(p_usage->'attempts') GROUP BY 1
   ), fallback AS (SELECT 'answer'::text AS name,1::numeric AS n WHERE NOT EXISTS(SELECT 1 FROM counts)),
   all_phases AS (SELECT * FROM counts UNION ALL SELECT * FROM fallback)
   SELECT p.name,p.n,sum(p.n) OVER() AS total,row_number() OVER(ORDER BY p.name) AS pos,count(*) OVER() AS size
     FROM all_phases p ORDER BY p.name
 LOOP
   share:=CASE WHEN phase.pos=phase.size THEN remaining ELSE duration*phase.n/phase.total END;
   remaining:=remaining-share;
   part:=share*0.1*(r.pricing->'rates'->>'price_per_10ms')::numeric*1000000000;
   posted:=floor(part+carry)::bigint;carry:=part+carry-posted;projected:=projected+posted;
   INSERT INTO public.retail_contributions VALUES(
     'answer-compute:'||md5(jsonb_build_array(oid,r.job_id,p_answer,phase.name)::text),oid,r.job_id,phase.name,'compute',
     0,0,share,r.pricing->'rates',part,posted,1000000000,
     md5(p_usage::text||r.pricing::text||phase.name),extract(epoch from clock_timestamp()));
 END LOOP;
 exact:=((input_n+output_n)*(r.pricing->'rates'->>'price_per_token')::numeric+
   duration*0.1*(r.pricing->'rates'->>'price_per_10ms')::numeric)*1000000000+a.fractional_debit_units;
 actual:=floor(exact);remainder:=exact-actual;
 IF projected<>actual OR carry<>remainder THEN RAISE EXCEPTION 'answer display allocation differs from settlement'; END IF;after_balance:=a.balance_units::numeric+r.estimate_units-actual;
 IF actual>9223372036854775807 OR after_balance NOT BETWEEN -9223372036854775808 AND 9223372036854775807 THEN RAISE EXCEPTION 'answer arithmetic outside account integer range'; END IF;
 -- No overrun, balance, job-running or new-work gate at settlement. Usage already happened.
 PERFORM set_config('requi.ledger_kind','adjustment',true),set_config('requi.ledger_actor','system:answer_accounting',true),
   set_config('requi.ledger_reason','answer settlement',true),set_config('requi.ledger_reference',p_answer,true);
 UPDATE public.credit_accounts SET balance_units=after_balance::bigint,fractional_debit_units=remainder,
   updated_at=extract(epoch from clock_timestamp()) WHERE owner_id=oid;
 UPDATE public.answer_charge_receipts SET state='settled',actual_units=actual::bigint,correction_units=actual::bigint-estimate_units,
   fractional_remainder=remainder,usage_manifest=p_usage,overrun=(actual>estimate_units),settled_at=clock_timestamp()
   WHERE answer_id=p_answer RETURNING * INTO r;
 -- Both outcomes persist in the receipt, so statistics include a denominator.
 -- Runtime logging will project this receipt after commit, not add a payment call.
 RETURN to_jsonb(r);
END $$;

-- Supabase default ACLs must not expose the new monetary API or evidence.
REVOKE ALL ON public.answer_charge_receipts,public.answer_charge_usage FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.requi_answer_start(text,text,text,text,jsonb),public.requi_answer_finish(text,jsonb) FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT,INSERT,UPDATE ON public.answer_charge_receipts TO requi_backend;
GRANT SELECT,INSERT ON public.answer_charge_usage TO requi_backend;
GRANT EXECUTE ON FUNCTION public.requi_answer_start(text,text,text,text,jsonb),public.requi_answer_finish(text,jsonb) TO requi_backend;
