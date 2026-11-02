"""Operator-only service routes. Shared pin repository, no Back office DB access."""
import json,os
from fastapi import Depends,HTTPException,Request
from pydantic import BaseModel
from . import auth,model_pins as pins,production_gateway as pg

class PinAction(BaseModel):
    payload:dict|None=None
    amount:str=''
    model:str='deepseek-flash'
    context:int=128000
    maximum_output:int=16384
    expected:str|None=None
    review:dict|None=None
    signature:str=''
    confirm:bool=False
    reason:str=''


def install(app,owner_store,reader,activate=None):
    def repo():
        operator=reader()
        try:operator.principal.authorize(owner_store,'services.operate')
        except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
        return pins.PinRepository(operator.db,operator.principal)
    def safe(action):
        try:return action()
        except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
        except (ValueError,KeyError,TypeError) as exc:raise HTTPException(409,{'error':str(exc),'next':'Open Back office > Debug > Download evidence; refresh Model pins and retry.'}) from exc
    @app.get('/api/operator/model-pins')
    def listing(a:auth.Actor=Depends(auth.require_actor)):
        repository=repo();history=repository.history(owner_store)
        rows=repository.list()
        result={'pins':[{**dict(r),'status':pins.badge(r['payload'])} for r in rows],'history':history,'bootstrap':[]}
        from .operator_expense_policy import read
        result['expense_policy']=read(repository,owner_store)
        if not rows:
            # Reviewable import, never an automatic write, never includes keys.
            try:
                prices=json.loads(os.environ.get(pg.PRICES_ENV,'[]'));contexts=json.loads(os.environ.get(pg.CEILING_ENV,'null'))
                for row in prices:
                    context=next(c for c in contexts if c.get('provider')==row['provider']) if isinstance(contexts,list) else contexts
                    context=dict(context);context.pop('provider',None)
                    result['bootstrap'].append(pins.validate({'row':row,'context':context}))
            except (ValueError,TypeError,KeyError,StopIteration):pass
        return result
    @app.post('/api/operator/model-pins/{action}')
    def operate(action:str,body:PinAction,request:Request,a:auth.Actor=Depends(auth.require_actor)):
        repository=repo()
        if request.headers.get('origin')!=str(request.base_url).rstrip('/') or request.headers.get('sec-fetch-site')=='cross-site':raise HTTPException(403,'Same-origin operator action required')
        def run():
            if action=='expense-policy':
                if not body.confirm:raise ValueError('Confirm the reviewed operator policy')
                from .operator_expense_policy import commit
                return {'revision':commit(repository,owner_store,body.amount,body.expected,body.reason),'status':'saved','activation':False}
            if action=='new-local':
                model=body.model
                value={'row':{'provider':'ollama','model':model,'price_version':'local-reviewed','source':'Operator reviewed local server bounds','expense_input':0,'expense_output':0,'retail_input':0,'retail_output':0,'retail_units_per_credit':10**9},'context':{'model':model,'context_tokens':body.context,'maximum_output_tokens':body.maximum_output,'source':'Operator reviewed local server bounds','bound_version':'local-reviewed','protective_run_ceiling':False}}
                return {**pins.proposal(pins.validate(value),None,a.id),'before':None}
            if action=='new':
                value=pins.new_deepseek(body.model,body.context,body.maximum_output)
                return {**pins.proposal(value,None,a.id),'before':None}
            if action in ('refresh','edit'):
                value=pins.validate(body.payload)
                rows=repository.list();current=next((r for r in rows if (r['provider'],r['model'])==(value['row']['provider'],value['row']['model'])),None)
                if (current['revision'] if current else None)!=body.expected:raise ValueError('Pin changed; reload the current row')
                if action=='refresh':
                    result=pins.refresh(current['payload'] if current else value)
                    if result['warning']:return result
                    value=result['payload']
                elif value['row']['provider']=='deepseek':
                    old=current['payload']['row']['provider_snapshot'] if current else value['row']['provider_snapshot']
                    # Manual changes never renew a vendor-fetch timestamp.
                    if not current:raise ValueError('Fetch vendor prices before importing a paid pin')
                    value['row']['provider_snapshot']['fetched_at']=old['fetched_at']
                return {**pins.proposal(pins.validate(value),body.expected,a.id),'before':current['payload'] if current else None}
            if action=='approve':
                if body.confirm is not True:raise ValueError('Confirm the reviewed diff')
                value=pins.approved_proposal(body.review,body.signature,a.id)
                rev=repository.commit(owner_store,value,body.review['expected'],body.reason)
                warning=None
                if activate is not None:
                    try:activate()
                    except Exception as exc:warning='Pin saved; execution is still closed: '+str(exc)
                return {'warning':warning,'revision':rev,'status':'approved','message':'New runs use this pin. Existing runs keep their reviewed revision.'}
            raise ValueError('Unknown pin action')
        return safe(run)
