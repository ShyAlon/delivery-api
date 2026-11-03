"""Read-only acceptance readiness used by the owner CLI and Back office.

Checks never grant activation, create a login, change a policy or call a model.
"""
from decimal import Decimal


def inspect(api,maximum=None):
    rows=[];data={}
    def check(name,path,predicate,reason):
        try:
            value=api('GET',path);data[name]=value
            ok=predicate(value)
        except Exception:
            ok=False
        rows.append({'check':name,'status':'pass' if ok else 'fail','reason':'' if ok else reason})
    check('session','/api/auth',lambda v:v.get('authenticated') is True,'Sign in as the reviewed operator; the session is missing or expired.')
    state=data.get('session',{})
    rows.append({'check':'target','status':'pass' if state.get('mode')=='supabase' else 'fail','reason':'' if state.get('mode')=='supabase' else 'Target must identify as the account service.'})
    rows.append({'check':'token/session','status':'pass' if state.get('authenticated') is True else 'fail','reason':'' if state.get('authenticated') is True else 'Use the current signed-in operator session.'})
    check('marker migration','/api/operations/test-tasks',lambda v:isinstance(v.get('tasks'),list),'Apply the owner test-task migration and verify reviewed owner permission.')
    check('default pin','/api/operator/model-pins',lambda v:len([p for p in v.get('pins',[]) if p.get('provider')=='deepseek' and p.get('is_default')])==1,'Review and approve exactly one default DeepSeek pin in Model pins.')
    policy=data.get('default pin',{}).get('expense_policy',{}).get('current')
    try:
        ceiling=Decimal(policy['limit_units'])/Decimal(policy['units_per_usd'])
        ok=ceiling.is_finite() and ceiling>0 and (maximum is None or ceiling<=maximum)
    except (TypeError,KeyError,ArithmeticError):ceiling=None;ok=False
    rows.append({'check':'ceiling','status':'pass' if ok else 'fail','reason':'' if ok else 'Set a positive operator task-generation ceiling at or below the approved --max-usd.'})
    rows.append({'check':'run approval','status':'unverified' if maximum is None else 'pass','reason':'Choose a separate explicit --max-usd before paid execution.' if maximum is None else ''})
    return {'checks':rows,'ready':all(r['status']=='pass' for r in rows if r['check']!='run approval'),
            'activation_enabled':False,'data':data,'ceiling':ceiling}
