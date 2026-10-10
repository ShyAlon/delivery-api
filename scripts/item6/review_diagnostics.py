"""Read-only D13 review diagnostics. Field/category evidence, never config values."""
import ast
import importlib.util
import re
from pathlib import Path

class ReviewPrerequisite(ValueError):
    def __init__(self,checks):
        super().__init__('D13 review prerequisites failed')
        self.checks=checks


def safe_category(error):
    """Echo only fixed literals from owned validation code, never arbitrary strings."""
    known=set()
    paths=[Path(__file__).parent/name for name in ('single_config.py','model_data.py','vendor_config.py','isolation.py')]
    for name in ('delivery_api.paypal_adapter','delivery_api.provider_snapshot'):
        try:
            spec=importlib.util.find_spec(name)
            if spec and spec.origin:paths.append(Path(spec.origin))
        except (ImportError,ValueError):pass
    for path in paths:
        try:tree=ast.parse(path.read_text())
        except (OSError,SyntaxError):continue
        for node in ast.walk(tree):
            if isinstance(node,ast.Raise) and isinstance(node.exc,ast.Call) and node.exc.args and isinstance(node.exc.args[0],ast.Constant) and isinstance(node.exc.args[0].value,str):known.add(node.exc.args[0].value)
    for label in ('price_per_token','price_per_10ms','usd_per_credit'):
        for requirement in ('finite nonnegative decimal required','positive decimal required','rate not representable at 1000000000 units per credit'):
            known.add(label+': '+requirement)
    return str(error) if isinstance(error,ValueError) and str(error) in known else type(error).__name__+'; private exception values not printed'


def binding_checks(values):
    checks=[]
    for field in ('SANDBOX_MERCHANT_ID','SANDBOX_WEBHOOK_ID'):
        value=values.get(field)
        ok=isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,127}',value) is not None
        found='valid format' if ok else ('unresolved/null' if value is None else 'wrong type' if not isinstance(value,str) else 'invalid resource-ID characters, empty or outside allowed size; value not printed')
        checks.append({'checked':field+' PayPal resource-ID format','expected':'JSON string; 1-127 ASCII letters/digits/underscore/hyphen; no spaces, URLs or labels','found':found,'where':'private/.env:'+field,'ok':ok})
    return checks


def require_bindings(values):
    bad=[c for c in binding_checks(values) if not c['ok']]
    if bad:raise ReviewPrerequisite(bad)


def diagnose_review(root):
    from single_config import root_check,load_env,unresolved
    from delivery_api.retail_pricing import RetailRates
    from vendor_config import BASE,RETAIL,REMOVED,load_snapshot
    root=Path(root).absolute();checks=[]
    def record(checked,expected,found,where,ok):checks.append({'checked':checked,'expected':expected,'found':found,'where':where,'ok':ok})
    try:root_check(root)
    except Exception as error:
        record('run identity/file safety','private non-symlink run, false launch/run flags and empty evidence',safe_category(error),str(root),False)
        return result(checks)
    try:values,_=load_env(root)
    except Exception as error:
        record('.env syntax/schema/safety','secure JSON-scalar source with known fields',safe_category(error),'private/.env',False)
        return result(checks)
    checks.extend(binding_checks(values))
    for field in sorted(BASE):
        if field in ('SANDBOX_MERCHANT_ID','SANDBOX_WEBHOOK_ID'):continue
        value=values.get(field);ok=isinstance(value,str) and not unresolved(value)
        record(field+' required local field','nonempty JSON string', 'present' if ok else 'missing/null, invalid type or unresolved placeholder; value not printed','private/.env:'+field,ok)
    legacy=REMOVED & values.keys()
    for field in sorted(legacy):record(field+' retired duplicate field','absent from migrated source','legacy field still present; use reviewed migration candidate','private/.env:'+field,False)
    for field in sorted(RETAIL):
        try:RetailRates.from_environment({field:values[field]} if field in values else {})
        except Exception as error:record(field+' optional override','finite nonnegative representable decimal; USD_PER_CREDIT positive',safe_category(error),'private/.env:'+field,False)
    model=values.get('MODEL')
    if isinstance(model,str):
        try:load_snapshot(root,model)
        except Exception as error:
            # Fixed categories, no arbitrary exception content or filename echo.
            record('generated vendor snapshot','one fresh hash-matching parseable snapshot matching fixture model',safe_category(error),'private/vendor-snapshot-*.json',False)
    return result(checks)


def result(checks):
    failures=[c for c in checks if not c['ok']]
    return {'action':'diagnose-review','review_ready':not failures,'checks':failures or [{'checked':'D13 local review prerequisites','expected':'valid local source/bindings/snapshot','found':'checks passed; no config values printed','where':'private/.env + vendor snapshot','ok':True}],
            'next':'Fix only reported local fields. Preserve keys, snapshot and run evidence; rerun review-config after checks pass.','run_started':False,'files_changed':False}
