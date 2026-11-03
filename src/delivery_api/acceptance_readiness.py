"""Read-only acceptance readiness used by the owner CLI and Back office.

Checks never grant activation, create a login, change a policy or call a model.
"""
from decimal import Decimal


def inspect(api,maximum=None):
    rows=[];data={};errors={}
    def check(name,path,predicate,reason):
        try:
            value=api('GET',path);data[name]=value
            ok=predicate(value)
        except Exception as error:
            errors[name]=getattr(error,'http_status',None)
            ok=False
        rows.append({'check':name,'status':'pass' if ok else 'fail','reason':'' if ok else reason})
    check('session','/api/auth',lambda v:v.get('authenticated') is True,'Sign in as the reviewed operator; the session is missing or expired.')
    state=data.get('session',{})
    rows.append({'check':'target','status':'pass' if state.get('mode')=='supabase' else 'fail','reason':'' if state.get('mode')=='supabase' else 'Target must identify as the account service.'})
    rows.append({'check':'token/session','status':'pass' if state.get('authenticated') is True else 'fail','reason':'' if state.get('authenticated') is True else 'Use the current signed-in operator session.'})
    check('marker migration','/api/operations/test-tasks',lambda v:isinstance(v.get('tasks'),list),'Apply the owner test-task migration and verify reviewed owner permission.')
    if errors.get('marker migration')==403:
        rows[-1]['reason']='The signed-in account lacks reviewed owner-test access. Review req backoffice-access --test-owner; apply only after its explicit confirmation.'
    check('default pin','/api/operator/model-pins',lambda v:len([p for p in v.get('pins',[]) if p.get('provider')=='deepseek' and p.get('is_default')])==1,'Review and approve exactly one default DeepSeek pin in Model pins.')
    policy=data.get('default pin',{}).get('expense_policy',{}).get('current')
    try:
        ceiling=Decimal(policy['limit_units'])/Decimal(policy['units_per_usd'])
        ok=ceiling.is_finite() and ceiling>0 and (maximum is None or ceiling<=maximum)
    except (TypeError,KeyError,ArithmeticError):ceiling=None;ok=False
    rows.append({'check':'ceiling','status':'pass' if ok else 'fail','reason':'' if ok else 'Set a positive operator task-generation ceiling at or below the approved --max-usd.'})
    rows.append({'check':'run approval','status':'unverified' if maximum is None else 'pass','reason':'Choose a separate explicit --max-usd before paid execution.' if maximum is None else ''})
    steps={
        'target':(1,'Connect to the preview','Start req preview --apply and verify the target.'),
        'session':(1,'Connect to the preview','Sign in as the reviewed owner.'),
        'token/session':(1,'Connect to the preview','Use the current signed-in owner session.'),
        'marker migration':(2,'Prepare owner test access','Review req migrate and req backoffice-access --test-owner.'),
        'default pin':(3,'Review paid settings','Open Model pins and approve one default DeepSeek pin.'),
        'ceiling':(3,'Review paid settings','Set the operator limit in Model pins.'),
        'run approval':(4,'Approve this run separately','Choose the run ceiling, then run scripts/ops/acceptance.sh --dry-run --max-usd YOUR_APPROVED_NUMBER.'),
    }
    for row in rows:
        number,title,action=steps[row['check']]
        row.update(step=number,group=title,next=action if row['status']!='pass' else 'No action needed.')
        if row['check']=='marker migration' and errors.get('marker migration')==403:row['next']='Review req backoffice-access --test-owner, then --test-owner --apply if approved.'
    rows.sort(key=lambda row:row['step'])
    return {'checks':rows,'ready':all(r['status']=='pass' for r in rows if r['check']!='run approval'),
            'activation_enabled':False,'data':data,'ceiling':ceiling}
