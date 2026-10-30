-- Standard operator Debug includes normalized usage reads, never payment/effect permissions.
-- Applies to the already-reviewed enabled operator; never chooses an identity or enables one.
CREATE FUNCTION public.requi_operator_usage_default() RETURNS trigger
LANGUAGE plpgsql SET search_path='' AS $$
BEGIN
  IF NEW.enabled AND NOT ('usage.read'=ANY(NEW.capabilities)) THEN
    NEW.capabilities := array_append(NEW.capabilities,'usage.read');
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.requi_operator_usage_default() FROM PUBLIC;
CREATE TRIGGER operator_usage_default BEFORE INSERT OR UPDATE OF enabled ON public.operator_principals
  FOR EACH ROW EXECUTE FUNCTION public.requi_operator_usage_default();
UPDATE public.operator_principals SET capabilities=array_append(capabilities,'usage.read')
  WHERE enabled AND NOT ('usage.read'=ANY(capabilities));
-- Existing least-privilege role, no extra table/column grants or BYPASSRLS.
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='delivery_app') THEN
  GRANT requi_operator_usage TO delivery_app WITH INHERIT FALSE, SET TRUE;
 END IF;
END $$;
