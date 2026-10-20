-- Complete, append-only history of every credit balance change, written by the database itself.
-- A trigger on credit_accounts records each change to balance_units (or units_per_credit) in the same
-- transaction as the change, so no code path can move credits without leaving a row. The application,
-- the grant script and the scale migration say who, why and which reference by setting four
-- transaction-local settings (requi.ledger_kind, _actor, _reason, _reference) before the write. A change
-- that sets nothing is still recorded, as kind 'unattributed', so a missing reason is visible and cannot hide.
-- No history is backfilled: the first recorded change starts the history, and its "before" balance is
-- whatever the account held at that moment.
-- Rows carry no foreign key to auth.users on purpose: the history outlives the account.
CREATE TABLE public.credit_ledger (
 entry_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 owner_id UUID NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('topup','grant','debit','adjustment','rescale','unattributed')),
 delta_units BIGINT NOT NULL,
 balance_before_units BIGINT NOT NULL,
 balance_after_units BIGINT NOT NULL,
 units_per_credit BIGINT NOT NULL CHECK(units_per_credit>0),
 before_units_per_credit BIGINT NOT NULL CHECK(before_units_per_credit>0),
 actor TEXT NOT NULL CHECK(length(actor) BETWEEN 1 AND 200),
 reason TEXT CHECK(reason IS NULL OR length(reason)<=500),
 reference TEXT CHECK(reference IS NULL OR length(reference)<=240),
 db_role TEXT NOT NULL,
 txid BIGINT NOT NULL,
 recorded_at DOUBLE PRECISION NOT NULL CHECK(recorded_at>=0 AND recorded_at<'Infinity'::double precision),
 CHECK(before_units_per_credit<>units_per_credit OR balance_after_units::numeric=balance_before_units::numeric+delta_units::numeric)
);
CREATE INDEX credit_ledger_owner_entry ON public.credit_ledger(owner_id,entry_id DESC);
ALTER TABLE public.credit_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.credit_ledger FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.credit_ledger FROM PUBLIC,authenticated;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
  REVOKE ALL ON public.credit_ledger FROM anon;
 END IF;
END $$;
-- Readers: the signed-in owner reads their own rows; the operator role reads through its existing registry check.
GRANT SELECT ON public.credit_ledger TO requi_backend;
CREATE POLICY owner_credit_ledger_read ON public.credit_ledger FOR SELECT TO requi_backend
 USING(public.requi_session_active() AND owner_id=auth.uid());
GRANT SELECT ON public.credit_ledger TO requi_operator_read;
CREATE POLICY operator_credit_ledger_read ON public.credit_ledger FOR SELECT TO requi_operator_read
 USING(public.requi_operator_read_allowed('analytics.read'));
-- The trigger function runs as the role applying this migration. FORCE ROW LEVEL SECURITY applies to that
-- role too, so it gets one INSERT-only policy; nobody gets UPDATE or DELETE.
DO $$ BEGIN
 EXECUTE format('CREATE POLICY credit_ledger_trigger_insert ON public.credit_ledger FOR INSERT TO %I WITH CHECK(true)',current_user);
END $$;
CREATE FUNCTION public.requi_credit_ledger_write() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE
 k TEXT:=NULLIF(current_setting('requi.ledger_kind',true),'');
 before_units BIGINT; before_scale BIGINT; moved BIGINT;
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.balance_units=0 THEN RETURN NEW; END IF;
  before_units:=0; before_scale:=NEW.units_per_credit;
 ELSE
  IF OLD.balance_units=NEW.balance_units AND OLD.units_per_credit=NEW.units_per_credit THEN RETURN NEW; END IF;
  before_units:=OLD.balance_units; before_scale:=OLD.units_per_credit;
 END IF;
 IF before_scale<>NEW.units_per_credit THEN
  k:='rescale';
  moved:=(NEW.balance_units::numeric-(before_units::numeric*NEW.units_per_credit::numeric/before_scale::numeric))::bigint;
 ELSE
  moved:=NEW.balance_units-before_units;
 END IF;
 IF k IS NULL OR k NOT IN ('topup','grant','debit','adjustment','rescale') THEN k:='unattributed'; END IF;
 INSERT INTO public.credit_ledger(owner_id,kind,delta_units,balance_before_units,balance_after_units,units_per_credit,before_units_per_credit,actor,reason,reference,db_role,txid,recorded_at)
 VALUES(NEW.owner_id,k,moved,before_units,NEW.balance_units,NEW.units_per_credit,before_scale,
  COALESCE(NULLIF(current_setting('requi.ledger_actor',true),''),'unattributed:'||session_user),
  NULLIF(current_setting('requi.ledger_reason',true),''),NULLIF(current_setting('requi.ledger_reference',true),''),
  session_user,txid_current(),extract(epoch from clock_timestamp()));
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.requi_credit_ledger_write() FROM PUBLIC,authenticated;
CREATE TRIGGER requi_credit_ledger_write AFTER INSERT OR UPDATE ON public.credit_accounts
 FOR EACH ROW EXECUTE FUNCTION public.requi_credit_ledger_write();
CREATE FUNCTION public.requi_credit_ledger_append_only() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 RAISE EXCEPTION 'credit_ledger is append-only: % is not allowed',TG_OP USING ERRCODE='insufficient_privilege';
END $$;
CREATE TRIGGER requi_credit_ledger_no_change BEFORE UPDATE OR DELETE ON public.credit_ledger
 FOR EACH ROW EXECUTE FUNCTION public.requi_credit_ledger_append_only();
CREATE TRIGGER requi_credit_ledger_no_truncate BEFORE TRUNCATE ON public.credit_ledger
 FOR EACH STATEMENT EXECUTE FUNCTION public.requi_credit_ledger_append_only();
-- Limit, stated plainly: a database owner or superuser can still drop these triggers. The ledger records what the
-- application and operator scripts do through the normal roles; it is not protection against the database owner.
