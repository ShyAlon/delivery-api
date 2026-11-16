-- Future-call diagnostics only. No backfill, debit, policy or model-call change.
-- Old runtimes keep writing NULL; completed evidence is immutable.
ALTER TABLE public.staged_provider_responses ADD COLUMN call_evidence jsonb
 CHECK(call_evidence IS NULL OR (jsonb_typeof(call_evidence)='object' AND call_evidence->>'version' IS NOT DISTINCT FROM 'call-metadata-v1' AND octet_length(call_evidence::text)<=16384));
CREATE INDEX staged_provider_call_evidence_attempt_idx ON public.staged_provider_responses(owner_id,attempt_id) WHERE attempt_id IS NOT NULL;
CREATE FUNCTION public.requi_call_evidence_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.call_evidence IS NOT NULL AND NEW.call_evidence IS DISTINCT FROM OLD.call_evidence OR
    OLD.response IS NOT NULL AND NEW.call_evidence IS DISTINCT FROM OLD.call_evidence THEN
  RAISE EXCEPTION 'completed call evidence is immutable';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER requi_call_evidence_guard BEFORE UPDATE ON public.staged_provider_responses
 FOR EACH ROW EXECUTE FUNCTION public.requi_call_evidence_guard();
REVOKE ALL ON FUNCTION public.requi_call_evidence_guard() FROM PUBLIC,anon,authenticated,service_role;

CREATE OR REPLACE FUNCTION public.requi_answer_finish(p_answer text,p_usage jsonb)
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
      item-ARRAY['attempt_id','phase','input_tokens','output_tokens','call_evidence']<>'{}'::jsonb OR
      jsonb_typeof(item->'attempt_id')<>'string' OR length(item->>'attempt_id') NOT BETWEEN 1 AND 240 OR
      jsonb_typeof(item->'phase')<>'string' OR length(item->>'phase') NOT BETWEEN 1 AND 200 OR
      jsonb_typeof(item->'input_tokens')<>'number' OR jsonb_typeof(item->'output_tokens')<>'number' THEN RAISE EXCEPTION 'invalid attempt usage'; END IF;
   IF item ? 'call_evidence' AND (jsonb_typeof(item->'call_evidence') IS DISTINCT FROM 'object' OR
      item->'call_evidence'->>'version' IS DISTINCT FROM 'call-metadata-v1' OR
      octet_length((item->'call_evidence')::text)>16384) THEN RAISE EXCEPTION 'bounded call evidence required'; END IF;
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
-- Catalog-only readiness sentinel. Existing receipt API and old-record reads remain compatible.
CREATE FUNCTION public.requi_call_evidence_version() RETURNS integer LANGUAGE sql IMMUTABLE AS 'SELECT 1';
REVOKE ALL ON FUNCTION public.requi_call_evidence_version() FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.requi_call_evidence_version() TO requi_backend;
